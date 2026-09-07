"""Project every skill player and quarterback with a game this week.

This is the core of the platform and it was missing. `build_prop_edges.py` only
ever produced a number for players a sportsbook happened to post a line on, which
was 38 players out of the 1,003 skill players and QBs on current depth charts.
A projection engine that only projects the players someone else already priced is
not a projection engine.

So this runs the other way round. Every eligible player on a current depth chart
with a game in the next two weeks gets a projection for every market their
position can produce, whether or not a line exists. Lines become an overlay on
top of that: `build_prop_edges.py` joins projections to odds where odds exist.

Same freshness contract as the edge builder. Depth chart rank, injuries, the
Vegas number, venue and opponent are all refreshed for the game being predicted
rather than inherited from a stored row that could be months old.

Writes point projection plus the full predicted distribution (p10 through p90),
so a range is available for any player, not only the ones with a posted line.
"""

import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp

ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
# One slate, not two.
#
# At 14 days the window straddles two game weeks, so a player with a Sunday and
# a following Thursday game appeared twice on a page headed "this week" with
# two different opponents and two different numbers. Eight days covers the
# longest single NFL week (Thursday through the following Monday) and nothing
# beyond it.
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "8"))

DDL = """
CREATE TABLE IF NOT EXISTS player_projections (
    id            BIGSERIAL PRIMARY KEY,
    player_id     TEXT NOT NULL,
    player_name   TEXT,
    position      TEXT,
    team          TEXT,
    opponent      TEXT,
    game_id       TEXT,
    game_date     DATE,
    market_code   TEXT NOT NULL,
    projection    DOUBLE PRECISION NOT NULL,
    p10           DOUBLE PRECISION,
    p25           DOUBLE PRECISION,
    p50           DOUBLE PRECISION,
    p75           DOUBLE PRECISION,
    p90           DOUBLE PRECISION,
    model_name    TEXT,
    is_starter    BOOLEAN,
    depth_rank    DOUBLE PRECISION,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (player_id, market_code, game_date)
);
CREATE INDEX IF NOT EXISTS idx_projections_player ON player_projections (player_id);
CREATE INDEX IF NOT EXISTS idx_projections_date   ON player_projections (game_date);
CREATE INDEX IF NOT EXISTS idx_projections_market ON player_projections (market_code);
"""


def slate(engine) -> pd.DataFrame:
    """Every eligible player with an upcoming game, and their latest features.

    Driven by the depth chart rather than by odds, which is the whole point.
    """
    return pd.read_sql(
        text("""
            WITH current_depth AS (
                SELECT DISTINCT ON (d.player_id)
                       d.player_id, MIN(d.depth_team) AS depth_rank
                FROM depth_charts d
                WHERE d.season = (SELECT MAX(season) FROM depth_charts)
                GROUP BY d.player_id, d.season, d.week
                ORDER BY d.player_id, d.season DESC, d.week DESC
            ),
            upcoming AS (
                SELECT game_id, game_date, home_team, away_team
                FROM nfl_games
                WHERE game_date >= CURRENT_DATE
                  AND game_date < CURRENT_DATE + make_interval(days => :days)
            ),
            latest_feat AS (
                SELECT DISTINCT ON (f.player_id, m.code)
                       f.player_id, m.code AS market_code, f.as_of_game_date,
                       f.mean, f.stddev, f.weighted_mean, f.trend,
                       f.aux_mean, f.aux_trend, f.recs_mean, f.recs_trend,
                       f.extra_features
                FROM player_market_features f
                JOIN prop_markets m ON m.id = f.market_id
                WHERE f.lookback = 5
                ORDER BY f.player_id, m.code, f.as_of_game_date DESC
            )
            SELECT p.external_id AS player_id, p.name AS player_name,
                   p.position, p.team, cd.depth_rank,
                   u.game_id, u.game_date, u.home_team, u.away_team,
                   CASE WHEN p.team = u.home_team THEN u.away_team ELSE u.home_team END
                       AS opponent,
                   lf.market_code, lf.as_of_game_date,
                   lf.mean, lf.stddev, lf.weighted_mean, lf.trend,
                   lf.aux_mean, lf.aux_trend, lf.recs_mean, lf.recs_trend,
                   lf.extra_features
            FROM players p
            JOIN current_depth cd ON cd.player_id = p.external_id
            JOIN upcoming u ON (u.home_team = p.team OR u.away_team = p.team)
            JOIN prop_markets m2 ON p.position = ANY(m2.eligible_positions)
            JOIN latest_feat lf
              ON lf.player_id = p.external_id AND lf.market_code = m2.code
            WHERE p.position IN ('QB', 'RB', 'WR', 'TE', 'FB')
        """),
        engine,
        params={"days": DAYS_AHEAD},
    )


def num(v) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def main():
    engine = create_engine(bp.DATABASE_URL, future=True)
    with engine.begin() as conn:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    df = slate(engine)
    if df.empty:
        raise SystemExit("no upcoming games with eligible players")

    ctx = bp.load_current_context(engine)
    stale_factors = bp.load_stale_role_factors(ARTIFACT_DIR)

    print(f"{df['player_id'].nunique()} players, {len(df)} player-market rows, "
          f"{df['game_id'].nunique()} games")

    cache = {}
    rows = []
    skipped = 0

    for market_code, grp in df.groupby("market_code"):
        if market_code not in cache:
            loaded = bp.load_model_meta(ARTIFACT_DIR, market_code, engine)
            if loaded is None:
                cache[market_code] = None
            else:
                meta, model = loaded
                lb = int(meta.get("lookback", 5))
                cache[market_code] = (
                    meta, model, bp.load_quantile_bundle(ARTIFACT_DIR, market_code, lb)
                )
        if cache[market_code] is None:
            skipped += len(grp)
            continue
        meta, model, quant = cache[market_code]
        feature_cols = meta.get("feature_cols", [])

        for r in grp.itertuples(index=False):
            extra = r.extra_features
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            extra = extra or {}

            base = {
                "mean": r.mean, "stddev": r.stddev,
                "weighted_mean": r.weighted_mean, "trend": r.trend,
                "aux_mean": r.aux_mean, "aux_trend": r.aux_trend,
                "recs_mean": r.recs_mean, "recs_trend": r.recs_trend,
            }
            feats = {
                c: num(base[c]) if c in base else num(extra.get(c, 0.0))
                for c in feature_cols
            }

            # Same freshness contract as the edge builder: everything knowable
            # before kickoff describes THIS game, not the stored row's game.
            feats["_last_game_date"] = r.as_of_game_date
            bp.apply_current_context(
                feats, ctx, player_id=r.player_id, team=r.team,
                event_date=r.game_date, position=r.position,
            )
            feats.pop("_last_game_date", None)

            x = pd.DataFrame([feats])
            pred = max(0.0, float(model.predict(x)[0]))

            # Players returning in a reduced role produce well under what their
            # stale window implies; the factor is measured per market and only
            # applied where it validated out of sample.
            sf = stale_factors.get(market_code)
            factor = 1.0
            if sf:
                demoted = feats.get("depth_rank_delta", 0.0) or 0.0
                stale_days = feats.get("days_since_last_game", 0.0) or 0.0
                if demoted >= 1 and stale_days > sf.get("stale_days", 60):
                    factor = float(sf["factor"])
                    pred *= factor

            qs = {}
            if market_code in bp.COUNT_MARKETS:
                # Small integer counts get a Poisson range built from the point
                # rate, for the same reason the edge builder does: quantile
                # regression on a 0-to-4 variable reproduces the population
                # shape rather than the player's, which produced a median of 1.6
                # beside a mean of 2.61 on the same row.
                _, qs = bp.count_distribution(pred, 0.5)
            elif quant is not None:
                raw = {q: max(0.0, float(qm.predict(x)[0]))
                       for q, qm in quant["models"].items()}
                # Show the calibrated range, not the raw one. The fitted
                # quantiles are biased high at the low end in every market, so
                # an uncorrected p10 is not a 10th percentile and the band it
                # draws is narrower than the truth.
                cal = bp.calibrated_quantiles(raw, quant.get("calibration"))
                qs = {q: v * factor for q, v in cal.items()}

            rows.append({
                "player_id": r.player_id, "player_name": r.player_name,
                "position": r.position, "team": r.team, "opponent": r.opponent,
                "game_id": r.game_id, "game_date": r.game_date,
                "market_code": market_code,
                "projection": pred,
                "p10": qs.get(0.10), "p25": qs.get(0.25), "p50": qs.get(0.50),
                "p75": qs.get(0.75), "p90": qs.get(0.90),
                "model_name": meta["model_name"],
                "depth_rank": feats.get("depth_rank"),
                "is_starter": (feats.get("depth_rank") or 99) <= 1,
            })

    if not rows:
        raise SystemExit("no projections produced")

    out = pd.DataFrame(rows).drop_duplicates(
        subset=["player_id", "market_code", "game_date"]
    )

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE player_projections"))
        out.to_sql("player_projections", conn, if_exists="append", index=False,
                   method="multi", chunksize=500)

    print(f"\nPROJECTIONS BUILT: {len(out)} rows")
    print(f"  players covered : {out['player_id'].nunique()}")
    print(f"  starters        : {out[out['is_starter']]['player_id'].nunique()}")
    if skipped:
        print(f"  skipped (no active model): {skipped} rows")
    print("\nby market:")
    print(out.groupby("market_code").agg(
        players=("player_id", "nunique"),
        mean_proj=("projection", "mean"),
    ).round(2).to_string())


if __name__ == "__main__":
    main()
