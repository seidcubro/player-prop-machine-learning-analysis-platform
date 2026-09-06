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
