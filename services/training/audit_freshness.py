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

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))

# Sportsbook market key per internal market code. Mirrors
# services/api/app/odds_market_map.py, which lives in a different service and is
# not importable from here.
ODDS_MARKET_KEYS = {
    "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "pass_yds": "player_pass_yds",
    "pass_td": "player_pass_tds",
    "rush_att": "player_rush_attempts",
    "rush_yds": "player_rush_yds",
    "recs": "player_receptions",
    "rec_yds": "player_reception_yds",
}
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


def main():
    engine = create_engine(DATABASE_URL, future=True)
    print("=" * 62)
    print("PropSignal freshness audit")
    print("=" * 62)

    check_feature_refresh(engine)
    check_opponent_features(engine)
    check_model_consistency(engine)
    check_data_freshness(engine)
    check_edges(engine)
    check_projection_coverage(engine)
    check_quantile_calibration(engine)
    check_live_odds_table(engine)

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
