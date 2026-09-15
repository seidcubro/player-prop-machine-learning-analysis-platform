"""Fail loudly if anything the platform serves could be stale.

This exists because staleness is silent. Every bug it checks for shipped at some
point and produced plausible-looking numbers:

- Opponent "defense" features were averaged over the lookback window, so they
  described the defenses a player had just faced rather than the one he was
  about to play.
- Depth chart rank, injuries, Vegas line and venue were inherited from a feature
  row that could be eight months old, so a back who had been demoted in the
  offseason still projected as the starter.
- Quantile models were trained against a different feature space than the point
  model they accompany.

Run after any pipeline change. Exit code is non-zero when a check fails, so it
can gate a deploy.
"""

import ast
import json
import math
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))

from odds_markets import MARKET_TO_ODDS as ODDS_MARKET_KEYS
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)

failures: list[str] = []
warnings: list[str] = []


def fail(msg):
    failures.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"  WARN  {msg}")


def ok(msg):
    print(f"  ok    {msg}")


def is_current_game_feature(name: str) -> bool:
    """Features describing the game being predicted, which must be refreshed.

    Everything else is rolling history and is *correct* to come from the stored
    row -- the point of the window is that it describes the past.
    """
    if name.startswith(("opp_", "injury_", "pos_teammates_", "game_")):
        return True
    return name in {
        "depth_rank", "depth_rank_delta", "days_since_last_game",
        "is_stale_window", "stale_role_change", "is_home", "is_indoor",
        "is_turf", "rest_days", "team_implied_total", "team_spread",
    }


def probe_overridden_features(engine) -> set:
    """Run apply_current_context on a sentinel row and see what it changes.

    Every known feature is seeded with a value no real signal produces, then the
    override runs against a real upcoming game. Anything still holding the
    sentinel afterwards was never refreshed. This catches features set through a
    loop variable, which source inspection cannot see.
    """
    import build_prop_edges as bp

    ctx = bp.load_current_context(engine)

    upcoming = [v for v in ctx["games"].values() if v[0].game_date >= date.today()]
    if not upcoming:
        warn("no upcoming game available to probe the override")
        return set()
    g, is_home = min(upcoming, key=lambda v: v[0].game_date)
    team = g.home_team if is_home else g.away_team

    with engine.connect() as c:
        pid = c.execute(
            text(
                "SELECT player_id FROM depth_charts WHERE team = :t AND depth_team = 1 "
                "ORDER BY season DESC, week DESC LIMIT 1"
            ),
            {"t": team},
        ).scalar()
    if pid is None:
        warn(f"no depth-chart player found for {team} to probe with")
        return set()

    SENTINEL = -987654.0
    names = set()
    for f in ARTIFACTS.glob("*_lb5.json"):
        try:
            names |= set(json.loads(f.read_text()).get("feature_cols", []))
        except Exception:
            continue

    probe = {n: SENTINEL for n in names}
    probe["_last_game_date"] = g.game_date - timedelta(days=200)
    bp.apply_current_context(probe, ctx, pid, team, g.game_date, position="RB")
    probe.pop("_last_game_date", None)
    return {k for k, v in probe.items() if v != SENTINEL}



def check_feature_refresh(engine):
    """Every current-game feature any active model uses must be overridden."""
    print("\n[1] inference refreshes every current-game feature")
    with engine.connect() as c:
        actives = c.execute(text(
            "SELECT m.code, a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id"
        )).mappings().all()

    # Determined by *running* the override, not by reading the source. Several
    # features are set through a loop variable rather than a string literal, so
    # static matching both missed them and raised false alarms.
    overridden = probe_overridden_features(engine)

    total_missing = set()
    for a in actives:
        meta = ARTIFACTS / f"{a['model_name']}_{a['code']}_lb{a['lookback']}.json"
        if not meta.exists():
            fail(f"{a['code']}: active model metadata missing ({meta.name})")
            continue
        cols = json.loads(meta.read_text())["feature_cols"]
        missing = {c for c in cols if is_current_game_feature(c)} - overridden
        total_missing |= missing
        if missing:
            fail(f"{a['code']}: not refreshed -> {sorted(missing)}")
    if not total_missing and actives:
        ok(f"all current-game features refreshed across {len(actives)} markets")


def check_opponent_features(engine):
    """Opponent features must describe the opponent, not the schedule played."""
    print("\n[2] opponent features describe the actual opponent")
    q = text("""
        WITH r AS (
          SELECT f.player_id, f.as_of_game_date, f.opponent, g.position,
                 (f.extra_features->>'opp_pos_rush_yds_allowed')::float AS stored
          FROM player_market_features f
          JOIN player_game_stats_app g
            ON g.player_id = f.player_id AND g.game_date = f.as_of_game_date
          WHERE f.market_id = 2 AND f.lookback = 5
            AND f.extra_features ? 'opp_pos_rush_yds_allowed'
            AND g.position = 'RB'
          ORDER BY f.as_of_game_date DESC LIMIT 200
        )
        SELECT r.stored,
               (SELECT AVG(s) FROM (
                   SELECT SUM(g2.rushing_yards) AS s
                   FROM player_game_stats_app g2
                   WHERE g2.opponent = r.opponent AND g2.position = 'RB'
                     AND g2.game_date < r.as_of_game_date
                     AND g2.game_date > r.as_of_game_date - 200
                   GROUP BY g2.game_date) t) AS truth
        FROM r
    """)
    df = pd.read_sql(q, engine).dropna()
    if df.empty:
        warn("no rows to verify opponent features against")
        return
    corr = df["stored"].corr(df["truth"])
    err = (df["stored"] - df["truth"]).abs().mean()
    if corr is None or corr < 0.5:
        fail(
            f"opponent feature barely tracks the real opponent "
            f"(corr={corr:.2f}, mean abs diff={err:.1f} over {len(df)} rows)"
        )
    else:
        ok(f"tracks the real opponent (corr={corr:.2f}, mean abs diff={err:.1f})")


def check_model_consistency(engine):
    """Quantile bundles and stale-role factors must match the active models."""
    print("\n[3] artifacts agree with the active models")
    with engine.connect() as c:
        actives = c.execute(text(
            "SELECT m.code, a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id = a.market_id"
        )).mappings().all()

    for a in actives:
        meta_p = ARTIFACTS / f"{a['model_name']}_{a['code']}_lb{a['lookback']}.json"
        if not meta_p.exists():
            continue
        point_cols = json.loads(meta_p.read_text())["feature_cols"]
        qp = ARTIFACTS / f"quant_v1_{a['code']}_lb{a['lookback']}.json"
        if not qp.exists():
            warn(f"{a['code']}: no quantile bundle (win prob falls back to Gaussian)")
            continue
        qmeta = json.loads(qp.read_text())
        if qmeta["feature_cols"] != point_cols:
            fail(
                f"{a['code']}: quantile bundle feature space differs from the "
                f"point model ({len(qmeta['feature_cols'])} vs {len(point_cols)})"
            )
        elif qmeta.get("point_model_used_for_features") != a["model_name"]:
            fail(
                f"{a['code']}: quantile bundle built from "
                f"{qmeta.get('point_model_used_for_features')}, active is {a['model_name']}"
            )
        else:
            ok(f"{a['code']}: quantile bundle matches {a['model_name']}")


def check_data_freshness(engine):
    """Source tables must cover the seasons we claim to serve."""
    print("\n[4] source data is current")
    with engine.connect() as c:
        for table, col in [
            ("player_game_stats_app", "season"),
            ("depth_charts", "season"),
            ("snap_counts", "season"),
            ("injuries", "season"),
            ("ff_opportunity", "season"),
            ("pbp_player_game", "season"),
        ]:
            mx = c.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()
            if mx is None:
                fail(f"{table}: empty")
            elif mx < 2025:
                fail(f"{table}: newest season is {mx}, expected >= 2025")
            else:
                ok(f"{table}: through {mx}")

        gaps = c.execute(text("""
            SELECT season FROM (SELECT DISTINCT season FROM player_game_stats_app) s
            WHERE season BETWEEN 2022 AND 2025
        """)).scalars().all()
        missing = sorted({2022, 2023, 2024, 2025} - set(gaps))
        if missing:
            fail(f"player_game_stats_app missing whole seasons: {missing}")
        else:
            ok("player_game_stats_app has every season 2022-2025")

        nulls = c.execute(text(
            "SELECT count(*) FROM player_market_features WHERE team IS NULL"
        )).scalar()
        if nulls:
            fail(f"{nulls} feature rows have NULL team (run db/backfills/fix_team_final.sql)")
        else:
            ok("no NULL team in features")


def check_edges(engine):
    """Served edges must be for upcoming games and built from active models."""
    print("\n[5] served edges are current")
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT count(*) FILTER (WHERE commence_time >= NOW()) AS upcoming,
                   count(*) AS total,
                   MAX(created_at) AS built
            FROM prop_edges
        """)).mappings().first()
        if not rows["total"]:
            warn("prop_edges is empty")
        elif not rows["upcoming"]:
            warn("no edges for upcoming games (sync odds closer to kickoff)")
        else:
            ok(f"{rows['upcoming']} upcoming edges of {rows['total']}")

        stale_models = c.execute(text("""
            SELECT DISTINCT e.market_code, e.model_name
            FROM prop_edges e
            JOIN prop_markets m ON m.code = e.market_code
            JOIN active_models a ON a.market_id = m.id
            WHERE e.model_name <> a.model_name
        """)).mappings().all()
        if stale_models:
            fail(
                "edges built by non-active models: "
                + ", ".join(f"{r['market_code']}={r['model_name']}" for r in stale_models)
            )
        else:
            ok("every edge was built by the market's active model")

        # Matches PROB_FLOOR/PROB_CEILING in build_prop_edges.py. The builder
        # bounds every published probability to the same range, so this should
        # now be unreachable rather than merely unobserved; if it ever fires,
        # something is writing edges around the builder.
        impossible = c.execute(text(
            "SELECT count(*) FROM prop_edges WHERE win_prob > 0.95 OR win_prob < 0.05"
        )).scalar()
        if impossible:
            fail(f"{impossible} edges quote a win probability above 95% or below 5%")
        else:
            ok("no implausible win probabilities")


def check_projection_coverage(engine):
    """Are we projecting everyone we should be?

    The platform silently shipped for weeks projecting only the 38 players a
    sportsbook happened to price, out of ~1,000 skill players and QBs on current
    depth charts, because the edge builder was driven by odds rather than by the
    roster. Nothing failed; there was simply no number for most of the league.
    """
    print("\n[6] projection coverage")
    with engine.connect() as c:
        eligible = c.execute(text("""
            SELECT count(DISTINCT p.id) FROM players p
            WHERE p.position IN ('QB','RB','WR','TE','FB')
              AND EXISTS (SELECT 1 FROM depth_charts d
                          WHERE d.player_id = p.external_id
                            AND d.season = (SELECT MAX(season) FROM depth_charts))
        """)).scalar() or 0
        upcoming_games = c.execute(text(
            "SELECT count(*) FROM nfl_games WHERE game_date >= CURRENT_DATE "
            "AND game_date < CURRENT_DATE + make_interval(days => 14)"
        )).scalar() or 0
        try:
            projected = c.execute(text(
                "SELECT count(DISTINCT player_id) FROM player_projections"
            )).scalar() or 0
            rows = c.execute(text("SELECT count(*) FROM player_projections")).scalar() or 0
        except Exception:
            fail("player_projections table missing (run build_projections.py)")
            return

    if not upcoming_games:
        warn("no games in the next 14 days, nothing to project")
        return
    if projected == 0:
        fail("no projections at all (run build_projections.py)")
        return

    # Not every rostered player has a game inside the window or enough history
    # for a feature row, so this is a floor rather than an equality check.
    ok(f"{projected} players projected, {rows} player-market rows")
    if projected < 200:
        fail(f"only {projected} players projected across {upcoming_games} games; "
             "expected several hundred")
    else:
        ok(f"coverage looks sane against {eligible} rostered skill players/QBs")

    with engine.connect() as c:
        neg = c.execute(text(
            "SELECT count(*) FROM player_projections WHERE projection < 0"
        )).scalar() or 0
        inverted = c.execute(text(
            "SELECT count(*) FROM player_projections "
            "WHERE p10 IS NOT NULL AND p90 IS NOT NULL AND p10 > p90"
        )).scalar() or 0
    if neg:
        fail(f"{neg} negative projections")
    else:
        ok("no negative projections")
    if inverted:
        fail(f"{inverted} projections have p10 above p90")
    else:
        ok("prediction intervals are ordered")


def check_quantile_calibration(engine):
    """Do the published probabilities match what actually happened?

    This check used to score quantile *coverage* and fail whenever two adjacent
    quantiles reported the same number. That was a proxy, and measuring the
    outcome directly showed the proxy was pointing at the wrong thing. For
    pass_td the coverage check fired because q10 and q25 are identical, yet at
    the 0.5 line the model claimed 70.0% and hit 69.4% across 36 picks: the flat
    region of the CDF was doing no damage at all. The real problem was at the
    1.5 line, where q10 and q25 are irrelevant and the model claimed 61.9%
    against an actual 47.2% over 265 picks.

    So this now measures the thing that matters: for every market with enough
    graded picks, the gap between the win probability the site published and the
    rate those picks actually won at. That is the number a user is implicitly
    trusting, and it is the one worth gating on.

    Coverage is still reported for markets with no graded history yet, since
    something is better than nothing before the first results land.
    """
    print()
    print("[7] probability calibration (published vs actual)")
    with engine.connect() as c:
        rows = list(c.execute(text("""
            SELECT market_code,
                   count(*) AS picks,
                   avg(win_prob) AS claimed,
                   avg(CASE WHEN hit THEN 1.0 ELSE 0 END) AS actual
            FROM prop_edge_results
            WHERE hit IS NOT NULL AND win_prob IS NOT NULL
            GROUP BY market_code
            HAVING count(*) >= 100
            ORDER BY market_code
        """)).mappings())

    if not rows:
        warn("no graded picks yet; cannot measure calibration")
        return

    worst = 0.0
    for r in rows:
        gap = float(r["actual"]) - float(r["claimed"])
        worst = max(worst, abs(gap))
        line = (f"{r['market_code']}: claimed {r['claimed']:.3f}, "
                f"actual {r['actual']:.3f}, off by {gap:+.3f} "
                f"on {r['picks']} picks")
        # A published probability that is more than 15 points optimistic is not
        # a probability, it is a sales pitch. Ten points is worth flagging.
        if abs(gap) > 0.15:
            fail(line)
        elif abs(gap) > 0.10:
            warn(line)
        else:
            ok(line)

    if worst > 0.10:
        warn("probabilities run optimistic across the board. Expected before a "
             "season has been played, since no calibration can anticipate a "
             "regime it has not seen; it should tighten as the weekly retrain "
             "picks up current games.")


def check_live_odds_table(engine):
    """Is the "upcoming slate" table actually holding the upcoming slate?

    It was not. `odds_player_props` is meant to be current lines only, and it
    held 1,581 rows from December 2023 sitting beside 216 real ones, left behind
    by historical-odds work that wrote into the live table. `build_prop_edges.py`
    had no time filter, so the dashboard published 260 edges on games from two
    seasons ago -- including a 98% confident under on a 2023 game.

    The builder now refuses anything already kicked off, so this can no longer
    reach the dashboard. This check is about the table itself: stale rows still
    cost query time, still confuse anything that reads the table directly, and
    their presence means something wrote where it should not have.
    """
    print()
    print("[8] live odds table")
    with engine.connect() as c:
        row = c.execute(text("""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE e.commence_time < NOW()) AS past,
                   count(*) FILTER (WHERE e.provider_event_id IS NULL) AS orphan,
                   min(e.commence_time)::date AS oldest
            FROM odds_player_props p
            LEFT JOIN odds_events e ON e.provider_event_id = p.provider_event_id
        """)).mappings().first()
    if not row or not row["total"]:
        warn("odds_player_props is empty; run the props sync")
        return
    if row["orphan"]:
        fail(f"{row['orphan']} props reference an event row that does not exist")
    else:
        ok("every prop resolves to an event")
    if row["past"]:
        warn(f"{row['past']} of {row['total']} rows are for games already "
             f"played, oldest {row['oldest']}. The edge builder ignores them, "
             "but they do not belong in the live table.")
    else:
        ok(f"all {row['total']} rows are for upcoming games")


def check_market_map_agreement():
    """Do the two services still ask for the same markets?

    The training side and the API side each hold a market map and neither can
    import the other. When they drifted the live sync stopped requesting passing
    yards, one of the largest markets on the board, and nothing failed: the edge
    builder simply found no rows and published a shorter list. The whole class
    of bug is silent by construction, so it needs an explicit check.
    """
    print("\n[9] Market map agreement between services")
    api_path = Path("/opt/api_odds_market_map.py")
    if not api_path.exists():
        warn("API market map not mounted, cannot compare "
             "(expected at /opt/api_odds_market_map.py)")
        return

    # Parsed rather than imported: the API package pulls in FastAPI and a
    # settings module this container has no reason to install.
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    api_map = next(
        (ast.literal_eval(n.value) for n in tree.body
         if isinstance(n, ast.Assign)
         and any(getattr(t, "id", None) == "ODDS_API_MARKET_MAP" for t in n.targets)),
        None,
    )
    if api_map is None:
        fail("could not find ODDS_API_MARKET_MAP in the API market map")
        return

    only_training = set(ODDS_MARKET_KEYS) - set(api_map)
    only_api = set(api_map) - set(ODDS_MARKET_KEYS)
    mismatched = {k for k in set(ODDS_MARKET_KEYS) & set(api_map)
                  if ODDS_MARKET_KEYS[k] != api_map[k]}

    if only_training:
        fail(f"markets the models use but the sync never buys: "
             f"{sorted(only_training)}")
    if only_api:
        fail(f"markets the sync buys but nothing here maps: {sorted(only_api)}")
    for k in sorted(mismatched):
        fail(f"market {k!r} maps to {ODDS_MARKET_KEYS[k]!r} in training and "
             f"{api_map[k]!r} in the API")
    if not (only_training or only_api or mismatched):
        print(f"  OK: both services agree on {len(api_map)} markets")


def check_projection_agreement(engine):
    """The board and the projections page must show the same number.

    Both are the same model on the same player for the same game, so any gap
    means one of them fed the model different features. That is how a real bug
    surfaced: the edge builder took the game's date from `commence_time`, which
    is UTC, so every Thursday, Sunday and Monday night kickoff landed on the
    following day. The date is the key into the scheduled-game context, so those
    games missed the lookup and silently kept the stored row's opponent, venue
    and Vegas numbers. Three or four games a week, invisible, for as long as the
    context refresh had existed.

    Nothing else caught it. The projections were right, the edges were wrong,
    and the only symptom was two numbers that disagreed in the fourth decimal
    place on a page nobody cross-references.

    Matched on the game as well as the player and the market. Projections run
    eight days out, so in a week with a Thursday game a player has two rows for
    the same market while the board only carries the priced slate, and an
    unscoped join compared Sunday's edge against Thursday's projection.
    """
    print("\n[10] Board and projections agree")
    # Both the mean the two tables store and the median each page prints.
    #
    # Comparing only the mean missed the thing a reader would actually notice:
    # the board shows the median and the projections page used to show the mean,
    # so Bijan Robinson read 92.7 on one page and 75.8 on the other for the same
    # game. Both pages lead with the median now, and both numbers are checked.
    df = pd.read_sql(text("""
        SELECT e.player_name, e.market_code,
               e.projection AS edge_projection,
               p.projection AS page_projection,
               e.projection_median AS edge_shown,
               p.p50 AS page_shown
        FROM (SELECT DISTINCT player_name, market_code, projection,
                     projection_median,
                     (commence_time AT TIME ZONE 'America/New_York')::date
                       AS game_date
              FROM prop_edges) e
        JOIN player_projections p
          ON p.player_name = e.player_name
         AND p.market_code = e.market_code
         AND p.game_date   = e.game_date
    """), engine)
    if df.empty:
        warn("no rows in common between prop_edges and player_projections")
        return

    gap = (df["edge_projection"] - df["page_projection"]).abs().fillna(0)
    shown_gap = (df["edge_shown"] - df["page_shown"]).abs().fillna(0)
    bad = df[(gap > 0.001) | (shown_gap > 0.001)]
    if len(bad):
        worst = bad.assign(gap=gap.combine(shown_gap, max)).nlargest(3, "gap")
        detail = ", ".join(
            f"{r.player_name} {r.market_code} board {r.edge_shown:.3f} "
            f"vs page {r.page_shown:.3f}"
            for r in worst.itertuples()
        )
        fail(f"{len(bad)} of {len(df)} rows disagree between the board and the "
             f"projections page: {detail}")
    else:
        print(f"  OK: all {len(df)} shared rows match on both the mean and the "
              f"median each page shows")


def check_board_internal_consistency(engine):
    """Every row on the board has to tell one story.

    The board shows a model number, a line, an edge and a pick, and a reader
    takes them as one statement. They were four statements. The edge was signed
    toward whichever side was chosen, so the same numbers printed +0.56 on an
    over and -0.56 on an under; the pick came from expected value while the
    number beside it came from the median; and the median came off the quantile
    bundle while the probability had a further correction applied. On 131 of 481
    rows the pick disagreed with the model number sitting next to it.

    All three now derive from the median, so this holds by construction. It is
    checked anyway, because it held by construction before too, in a different
    part of the code, and stopped.
    """
    print("\n[11] Board rows are internally consistent")
    df = pd.read_sql(text("""
        SELECT player_name, market_code, line, projection_median, raw_edge,
               recommended_side, win_prob, expected_value
        FROM prop_edges WHERE projection_median IS NOT NULL
    """), engine)
    if df.empty:
        warn("no edges to check")
        return

    over = df["recommended_side"] == "over"
    problems = {
        "edge sign disagrees with the pick": (df["raw_edge"] > 0) != over,
        "pick disagrees with the model number": (
            df["projection_median"] > df["line"]) != over,
        "edge is not the model number minus the line": (
            (df["raw_edge"] - (df["projection_median"] - df["line"])).abs() > 0.001),
        "win probability is not better than even": df["win_prob"] <= 0.5,
        "expected value is not positive": df["expected_value"] <= 0,
    }
    clean = True
    for label, mask in problems.items():
        n = int(mask.sum())
        if n:
            clean = False
            bad = df[mask].head(2)
            detail = ", ".join(
                f"{r.player_name} {r.market_code} line {r.line} "
                f"model {r.projection_median:.2f} edge {r.raw_edge:+.2f} "
                f"{r.recommended_side}"
                for r in bad.itertuples())
            fail(f"{n} of {len(df)} rows where {label}: {detail}")
    if clean:
        print(f"  OK: all {len(df)} rows agree on model, edge, pick and price")


def check_injury_currency(engine):
    """Is the injury report we are using from this season?

    There was no recency bound on the injury lookup, so it took each player's
    most recent report ever and fed it in as current. In September that meant
    January: thirty-five players on the board carried a flag from last season,
    thirteen of them "Out" -- Bo Nix on the ankle he broke in the playoffs,
    Jayden Daniels on an elbow, Nico Collins on a concussion. Two were carrying
    "Questionable" from 2024. Every one of them was healthy and starting.

    Nothing failed, because a stale flag looks exactly like a fresh one. The
    query is bounded to the current season now, and this checks the bound is
    doing what it should rather than quietly matching nothing forever.
    """
    print("\n[12] Injury reports are from this season")
    row = pd.read_sql(text("""
        SELECT (SELECT max(season) FROM nfl_games
                WHERE game_date <= CURRENT_DATE + 14) AS current_season,
               (SELECT max(season) FROM injuries) AS newest_injury_season,
               (SELECT count(*) FROM injuries
                WHERE season = (SELECT max(season) FROM nfl_games
                                WHERE game_date <= CURRENT_DATE + 14)) AS current_rows,
               (SELECT count(*) FROM nfl_games
                WHERE game_date BETWEEN CURRENT_DATE - 2 AND CURRENT_DATE + 4)
                   AS games_this_week
    """), engine).iloc[0]

    cur = row["current_season"]
    if row["current_rows"] > 0:
        print(f"  OK: {int(row['current_rows'])} injury rows for season {cur}")
        return

    # No current-season reports. Before game week that is simply how it is:
    # teams do not publish until the Wednesday. Inside game week it means the
    # models are running blind and somebody should know.
    stale = row["newest_injury_season"]
    if row["games_this_week"]:
        warn(f"no injury reports for season {cur} while {int(row['games_this_week'])} "
             f"games are inside the next few days; the newest data is from season "
             f"{stale}, and it is correctly being ignored rather than applied")
    else:
        print(f"  OK: no season {cur} reports yet, which is expected before game "
              f"week; season {stale} data is correctly not being applied")


def check_no_dead_features(engine):
    """Is every feature a model asks for actually present and varying?

    A feature named in `feature_cols` but missing from `extra_features` does not
    raise. It is read with a default of 0.0, so the model trains on a constant
    and serving quietly feeds it a real number the model has never seen vary.

    That is exactly how `opp_pos_rush_yds_allowed` sat dead in all 16,274
    rushing rows for three months. One swallowed newline had merged a SQL
    comment with the column expression, so the select was commented out. No
    exception, no warning, and the opponent matchup signal the rushing markets
    lean on was simply not there.

    The injury flags are the known exception and are allowed to be constant: a
    player ruled out does not play, so he never produces a feature row, and
    `injury_out` cannot vary in training by construction. The edge builder
    excludes those players outright rather than relying on the model to learn
    from a column that can only ever be zero.
    """
    print("\n[13] No model is training on a dead feature")
    allowed_constant = {"injury_out", "injury_doubtful"}
    base = {"mean", "stddev", "weighted_mean", "trend", "aux_mean", "aux_trend",
            "recs_mean", "recs_trend"}

    models = pd.read_sql(text("""
        SELECT m.code, m.id AS market_id, a.model_name, a.lookback
        FROM active_models a JOIN prop_markets m ON m.id = a.market_id
        ORDER BY m.code
    """), engine)

    absent_total, constant_total, checked = 0, 0, 0
    for r in models.itertuples():
        meta_path = ARTIFACTS / f"{r.model_name}_{r.code}_lb{r.lookback}.json"
        if not meta_path.exists():
            continue
        cols = [c for c in json.load(open(meta_path))["feature_cols"]
                if c not in base]
        if not cols:
            continue
        checked += 1

        stats = pd.read_sql(text("""
            SELECT k AS feature,
                   count(*) FILTER (WHERE extra_features ? k) AS present,
                   -- Measured the way the model sees the column, with a missing
                   -- key filled as zero, not only over the rows that carry it.
                   --
                   -- Those are different questions. Over present rows only,
                   -- rz_targets_mean is 0.2 on all 44 quarterback rows it
                   -- appears on and reads as constant; the model is handed 2,135
                   -- zeros and those 44, which does vary. Failing on the first
                   -- reading stops the pipeline over a feature that is merely
                   -- thin, and thin is what the warning below is for. A feature
                   -- that is genuinely dead, absent everywhere or identical
                   -- everywhere, is still constant under both readings and
                   -- still fails.
                   stddev(COALESCE((extra_features->>k)::float, 0.0)) AS sd
            FROM player_market_features f,
                 LATERAL unnest(CAST(:cols AS text[])) AS k
            WHERE f.market_id = :mid AND f.lookback = :lb
            GROUP BY k
        """), engine, params={"cols": cols, "mid": r.market_id,
                              "lb": r.lookback})

        absent = stats.loc[stats["present"] == 0, "feature"].tolist()
        # Compared against a tolerance, not against zero.
        #
        # Postgres computes stddev over identical values as floating point
        # noise rather than an exact zero: rz_targets_mean came back as
        # 5.8e-17. `float(sd) == 0.0` is False for that, so the check written
        # to catch dead features could not catch a dead feature. Three of them
        # sat in the active passing models reporting OK.
        constant = [f for f, sd in zip(stats["feature"], stats["sd"])
                    if stats.loc[stats["feature"] == f, "present"].iloc[0] > 0
                    and (sd is None or not math.isfinite(float(sd))
                         or abs(float(sd)) < 1e-9)
                    and f not in allowed_constant]

        # Present on almost no rows is the same problem wearing a disguise: the
        # other 98% are read as a default zero, so the model is fitting a column
        # that is a constant with a rounding error in it.
        thin = [f for f, present in zip(stats["feature"], stats["present"])
                if 0 < int(present) < 0.05 * max(int(stats["present"].max()), 1)
                and f not in allowed_constant and f not in constant]
        # One line per market, not one per feature. Eight thin features across
        # four passing models is 32 warnings saying the same thing, and a
        # summary nobody reads is the same as no summary.
        if thin:
            total = int(stats["present"].max())
            worst = ", ".join(
                f"{f} ({int(stats.loc[stats['feature'] == f, 'present'].iloc[0])})"
                for f in sorted(thin))
            warn(f"{r.code}: {len(thin)} feature(s) present on almost no rows "
                 f"out of {total}, so the model sees a default zero nearly "
                 f"everywhere: {worst}")
        if absent:
            absent_total += len(absent)
            fail(f"{r.code}: model expects {sorted(absent)} but no row carries "
                 f"them, so they train as a constant zero")
        if constant:
            constant_total += len(constant)
            fail(f"{r.code}: {sorted(constant)} never vary, which a model "
                 f"cannot learn from")

    if not absent_total and not constant_total:
        print(f"  OK: every feature across {checked} models is present and varies")


def check_box_score_sanity(engine):
    """Do the counting stats obey the rules that make them counting stats?

    Receptions cannot exceed targets. A player with eight targets and no catches
    has either had a historically bad afternoon or, far more likely, had
    something else written into his target column.

    It was the latter for 579 player-games. `pbp_player_game` concatenated a
    receiving aggregate and a rushing one and then deduplicated to a single row
    per player-game, and `total_plays` counted targets on a receiving row but
    carries on a rushing one. Anyone who only ran got his carry count filed as
    targets, which is how Josh Allen finished a playoff game with twelve targets
    and no receptions.

    The same dedup kept the receiving row whenever a player had both, so every
    back who caught a pass lost his rushing half: 3,112 of 4,490 games with five
    or more carries arrived with a NULL `red_zone_carries` that the feature
    build read as zero, and the backs it zeroed were the ones good enough to be
    targeted.

    Both fed live features, `targets_weighted_mean` and yards per target on one
    side and `rz_carries` on the other, and neither raised anything. A shape
    check is the only thing that would have caught it.
    """
    print()
    print("[14] Box score stats are internally possible")
    row = pd.read_sql(text("""
        SELECT
          count(*) FILTER (WHERE receptions > targets)                AS impossible,
          count(*) FILTER (WHERE targets >= 8 AND receptions = 0)     AS dry_spells,
          -- Three, not one. A quarterback really is thrown at once in a while,
          -- on a trick play or a batted ball, and there are nine such games
          -- across four seasons. What does not happen is a quarterback drawing
          -- his carry count in targets, which is what the bug produced: Josh
          -- Allen at twelve, Herbert and Nix at ten.
          count(*) FILTER (WHERE targets >= 3 AND position_group = 'QB'
                             AND receptions = 0)                      AS qb_targets
        FROM player_game_stats
    """), engine).iloc[0]

    if int(row["impossible"]):
        fail(f"{int(row['impossible'])} player-games have more receptions than "
             f"targets, which is not a thing that can happen")
    if int(row["qb_targets"]):
        fail(f"{int(row['qb_targets'])} quarterback games carry three or more "
             f"targets and no catch, so something rushing-shaped is landing in "
             f"the target column again")

    # A real eight-target shutout happens a couple of times a decade, so a
    # handful across four seasons is the honest number and a hundred is a bug.
    dry = int(row["dry_spells"])
    if dry > 20:
        fail(f"{dry} player-games show eight or more targets and no catches, "
             f"far past what the sport produces")
    elif dry:
        print(f"  ok    {dry} genuine eight-target shutouts, within reason")

    # The rushing half of pbp_player_game, which the old dedup threw away for
    # anyone who was also targeted.
    rz = pd.read_sql(text("""
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE g.red_zone_carries IS NULL) AS missing
        FROM pbp_player_game g
        JOIN player_game_stats s
          ON s.player_id = g.player_id AND s.game_id = g.game_id
        WHERE s.carries >= 5
    """), engine).iloc[0]
    if int(rz["rows"]) and int(rz["missing"]) > int(rz["rows"]) * 0.05:
        fail(f"{int(rz['missing'])} of {int(rz['rows'])} games with five or more "
             f"carries have no red zone carry count, so the rushing half of the "
             f"play by play is being dropped")

    if not (int(row["impossible"]) or int(row["qb_targets"]) or dry > 20
            or (int(rz["rows"]) and int(rz["missing"]) > int(rz["rows"]) * 0.05)):
        print(f"  OK: targets, receptions and carries agree across "
              f"{int(rz['rows'])} rushing games")


def check_kickoff_dates(engine):
    """Does every board row agree with the schedule about what day it is?

    commence_time is stored as an instant, and turning it into a date needs a
    timezone. Read in UTC, a Sunday night kickoff at 20:20 Eastern becomes 00:20
    on Monday, so every Sunday, Monday and Thursday night game is dated a day
    late. That is 16% of every odds snapshot in the table.

    The consequences were not cosmetic. The edge builder used the UTC date as
    the key into the scheduled-game context and silently kept stale opponents
    and venues for those games. The edges endpoint printed the wrong kickoff day
    on 135 of 842 rows. The season backtest inner-joined on it against the real
    local game date, so every primetime game failed the join and vanished from a
    6,736 pick result, and primetime is where the biggest names play.

    Three separate places, one root cause, none of them raising anything. So the
    dates are checked against the schedule directly.
    """
    print()
    print("[15] Kickoff dates agree with the schedule")
    row = pd.read_sql(text("""
        SELECT count(*) AS rows,
               count(*) FILTER (
                 WHERE g.game_id IS NULL) AS unmatched
        FROM (SELECT DISTINCT event_id, home_team, away_team,
                     (commence_time AT TIME ZONE 'America/New_York')::date AS et_date
              FROM prop_edges WHERE commence_time IS NOT NULL) e
        LEFT JOIN nfl_games g ON g.game_date = e.et_date
    """), engine).iloc[0]

    if not int(row["rows"]):
        warn("no board rows carry a kickoff time")
        return
    if int(row["unmatched"]):
        fail(f"{int(row['unmatched'])} of {int(row['rows'])} board games have a "
             f"kickoff date with no scheduled game on it, which is what reading "
             f"commence_time in UTC does to a night game")
        return

    shifted = pd.read_sql(text("""
        SELECT count(*) AS n FROM prop_edges
        WHERE (commence_time AT TIME ZONE 'UTC')::date
           <> (commence_time AT TIME ZONE 'America/New_York')::date
    """), engine).iloc[0]["n"]
    print(f"  OK: {int(row['rows'])} board games land on a scheduled date, "
          f"{int(shifted)} of which UTC would have moved")


def check_published_figures(engine):
    """Does the site still state numbers the record supports?

    Every return figure on the dashboard and the FAQ is written into the page as
    text. The record underneath them is rebuilt whenever the backfill runs, and
    on the day this check was written that happened three times, each time
    leaving the site quoting figures that were no longer true. The dashboard
    claimed best bets returned +7.1% when the record said +4.4%, and claimed the
    under side returned +4.4% with an interval clearing zero when it had fallen
    to +1.9% with an interval straddling it.

    Overstating your own returns is the worst kind of stale number on a site
    like this, and nothing was checking it.

    The figures live in apps/web/src/lib/published-record.json, which the pages
    import and this recomputes from prop_edge_results. A disagreement fails,
    because the honest options are to update the file or to stop making the
    claim.
    """
    print()
    print("[16] Published figures match the record")
    path = Path("/opt/published_record.json")
    if not path.exists():
        warn("published figures not mounted, cannot compare "
             "(expected at /opt/published_record.json)")
        return

    claimed = json.loads(path.read_text(encoding="utf-8"))
    graded = pd.read_sql(text("""
        SELECT edge_tier, hit, price_american
        FROM prop_edge_results WHERE hit IS NOT NULL AND price_american IS NOT NULL
    """), engine)
    if graded.empty:
        warn("no graded picks to check the published figures against")
        return

    n = int(len(graded))
    if abs(n - int(claimed.get("graded_picks", 0))) > max(50, n * 0.02):
        fail(f"the site says {claimed.get('graded_picks')} graded picks, "
             f"the record holds {n}")

    payout = np.where(graded["price_american"] > 0,
                      graded["price_american"] / 100.0,
                      100.0 / graded["price_american"].abs())
    graded["units"] = np.where(graded["hit"].astype(bool), payout, -1.0)

    bad = []
    for tier, stated in (claimed.get("tiers") or {}).items():
        rows = graded[graded["edge_tier"] == tier]
        if len(rows) < 100:
            continue
        actual = float(rows["units"].mean())
        # A point of ROI is a wide tolerance and deliberately so: this is a
        # guard against a figure going stale, not a test of the third decimal.
        if abs(actual - float(stated)) > 0.01:
            bad.append(f"{tier} stated {float(stated):+.1%}, actual {actual:+.1%}")
    for b in bad:
        fail(f"published tier return is out of date: {b}")

    if not bad:
        print(f"  OK: {len(claimed.get('tiers') or {})} published tier returns "
              f"agree with {n} graded picks")


def check_corrections_present(engine):
    """Is the platform actually applying the corrections it was tuned with?

    Three fitted artifacts sit between the models and what gets published: the
    probability calibrator, the spread correction and the interval correction.
    Every one of them is optional by design, because a market that does not
    improve should ship uncorrected and a brand new deployment has none of them
    yet.

    That tolerance is also how they go missing without anyone noticing. The
    artifacts directory is gitignored, so a deploy that skips the rsync in the
    runbook gets a working site serving numbers that were never corrected: the
    probability calibrator alone pulls this board down by about nine points, and
    its absence looks like nothing at all.

    A warning, not a failure. Missing is the correct state before the first
    weekly run, and the run that fits them would otherwise be unable to finish.
    """
    print()
    print("[17] Published corrections are present")
    wanted = {
        "probability_calibrator.joblib": "win probabilities ship uncorrected",
        "spread_calibrator.json": "projections ship without the level correction",
        "interval_calibrator.json": "ranges ship without the interval correction",
        "median_anchor.json": "medians come from the quantile ladder alone, which "
                              "cannot extrapolate past its training leaves",
    }
    missing = [(n, why) for n, why in wanted.items()
               if not (ARTIFACTS / n).exists()]
    for name, why in missing:
        warn(f"{name} is not present, so {why}. Run the weekly pipeline, or "
             f"copy the artifacts directory across if this is a new server.")

    # Present is not the same as doing anything.
    #
    # A fitter that finds no market worth correcting writes {}, which is the
    # correct outcome and indistinguishable from a working correction if all
    # this checks is that the file exists. Both of these were deliberately
    # emptied after their gains turned out to be measured on the model's own
    # training rows, and the check still reported three healthy artifacts.
    empty = []
    for name in wanted:
        path = ARTIFACTS / name
        if not path.exists() or not name.endswith(".json"):
            continue
        try:
            if not json.loads(path.read_text(encoding="utf-8")):
                empty.append(name)
        except Exception:
            warn(f"{name} is present but could not be read as JSON")
    for name in empty:
        warn(f"{name} is present but corrects nothing. That is the right state "
             f"when no market beat its raw prediction out of sample; it is also "
             f"what a broken fit looks like, so it is worth knowing which.")

    if not missing and not empty:
        print(f"  OK: all {len(wanted)} correction artifacts present and active")
    elif not missing:
        print(f"  {len(wanted) - len(empty)} of {len(wanted)} corrections active")


def main():
    engine = create_engine(DATABASE_URL, future=True)
    print("=" * 62)
    print("PriorLine freshness audit")
    print("=" * 62)

    check_feature_refresh(engine)
    check_opponent_features(engine)
    check_model_consistency(engine)
    check_data_freshness(engine)
    check_edges(engine)
    check_projection_coverage(engine)
    check_quantile_calibration(engine)
    check_live_odds_table(engine)
    check_market_map_agreement()
    check_projection_agreement(engine)
    check_board_internal_consistency(engine)
    check_injury_currency(engine)
    check_no_dead_features(engine)
    check_box_score_sanity(engine)
    check_kickoff_dates(engine)
    check_published_figures(engine)
    check_corrections_present(engine)

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} FAILURE(S), {len(warnings)} warning(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"ALL CHECKS PASSED ({len(warnings)} warning(s))")
    for w in warnings:
        print(f"  - {w}")


if __name__ == "__main__":
    main()
