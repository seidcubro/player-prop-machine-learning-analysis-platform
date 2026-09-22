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

import build_prop_edges as bp
import interval_calibration as ic
import median_anchor as ma
import pandas as pd
import spread_calibration as sc
import vacated_volume as vv
from sqlalchemy import create_engine, text

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
    -- The same number before the spread correction, carried through to history
    -- so the correction is never refitted on its own output.
    projection_raw DOUBLE PRECISION,
    p50_raw       DOUBLE PRECISION,
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

-- Survives the truncate above, one row per player, market and game. See
-- db/migrations/add_projection_history.sql for why this is separate.
CREATE TABLE IF NOT EXISTS player_projection_history (
    player_id     TEXT NOT NULL,
    player_name   TEXT,
    team          TEXT,
    opponent      TEXT,
    position      TEXT,
    market_code   TEXT NOT NULL,
    game_date     DATE NOT NULL,
    projection    DOUBLE PRECISION,
    projection_raw DOUBLE PRECISION,
    p50_raw       DOUBLE PRECISION,
    p10           DOUBLE PRECISION,
    p25           DOUBLE PRECISION,
    p50           DOUBLE PRECISION,
    p75           DOUBLE PRECISION,
    p90           DOUBLE PRECISION,
    model_name    TEXT,
    depth_rank    INTEGER,
    projected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (player_id, market_code, game_date)
);
CREATE INDEX IF NOT EXISTS idx_projection_history_player
    ON player_projection_history (player_id, game_date DESC);
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
            -- Only players who can actually take the field.
            --
            -- `players.status` is the roster designation and it is current:
            -- 1,108 ACT players recorded a touch in Week 1, 8 practice squad
            -- elevations did, and exactly one RES did, who was placed on
            -- reserve after the game. Without this filter a quarter of the
            -- projection table was people who cannot play. A.J. Brown was
            -- carrying a 41.8 yard receiving projection and a spot on the
            -- board's own depth listing while on injured reserve, and eight
            -- released players had a full slate of numbers.
            --
            -- DEV is kept because a practice squad player can be elevated on
            -- game day and some are. Everything else, reserve, PUP, released,
            -- suspended, retired, exempt, is somebody who will not be there.
            JOIN current_depth cd ON cd.player_id = p.external_id
            JOIN upcoming u ON (u.home_team = p.team OR u.away_team = p.team)
            JOIN prop_markets m2 ON p.position = ANY(m2.eligible_positions)
            JOIN latest_feat lf
              ON lf.player_id = p.external_id AND lf.market_code = m2.code
            WHERE p.position IN ('QB', 'RB', 'WR', 'TE', 'FB')
              AND p.status IN ('ACT', 'DEV')
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
    last_played = bp.load_last_played(engine)
    spread_cal = sc.load(ARTIFACT_DIR)
    interval_cal = ic.load(ARTIFACT_DIR)
    anchor_cal = ma.load(ARTIFACT_DIR)
    # Same adjustment the edge builder applies, built the same way, so a
    # backup quarterback reads the same number on both pages.
    vacated_vol = vv.VacatedVolume(engine, ctx, vv.load_params(ARTIFACT_DIR))

    print(f"{df['player_id'].nunique()} players, {len(df)} player-market rows, "
          f"{df['game_id'].nunique()} games")

    cache = {}
    rows = []
    skipped = 0
    ruled_out = 0
    inj_status = ctx["injuries"]

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
            # A player ruled out or doubtful is not projected at all.
            #
            # The edge builder already refused picks on these players, but this
            # page kept them: Sam Darnold was listed for Week 2 at 21.6 pass
            # attempts after Seattle ruled him out, beside the Drew Lock
            # projection that should have had them. A projection for someone
            # who is not dressing is not conservative, it is wrong, and it
            # reads as the site not knowing the news.
            if r.player_id in inj_status.index and (
                str(inj_status.at[r.player_id, "report_status"] or "").strip()
                in ("Out", "Doubtful")
            ):
                ruled_out += 1
                continue

            feats = {
                c: num(base[c]) if c in base else num(extra.get(c, 0.0))
                for c in feature_cols
            }

            # Same freshness contract as the edge builder: everything knowable
            # before kickoff describes THIS game, not the stored row's game.
            # Not as_of_game_date. On a serving row that is the game being
            # predicted, so staleness would read zero. See
            # build_prop_edges.resolve_last_played.
            feats["_last_game_date"] = bp.resolve_last_played(
                last_played, r.player_id, r.as_of_game_date, r.game_date)
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

            # Volume inherited from a ruled-out teammate.
            #
            # The point projection is raised to the level that workload
            # implies. The predicted range is handled after the quantiles are
            # built, because it has to be raised to the same level rather than
            # scaled by the point's ratio. See vacated_volume.factor.
            inherit = vacated_vol.factor(r.player_id, market_code, pred)
            if inherit != 1.0:
                pred *= inherit

            # Undo the flattening before anything is derived from the point
            # prediction, which is what the edge builder does.
            #
            # Correcting it afterwards put the two out of step on the count
            # markets: those build their median from a Poisson on the point
            # rate, so a rate corrected later gave the board a median of 1.00
            # against the page's 1.45 for the same Mahomes row. The freshness
            # audit caught it, which is what that check exists for.
            #
            # After the stale-role factor, so a demoted player is marked down
            # first and then read on the corrected scale.
            # Kept before the correction is applied. fit_spread_calibrator
            # learns from this history, so writing only the corrected number
            # would have it fitting a correction on top of its own output every
            # week. See db/migrations/add_projection_raw.sql.
            pred_raw = pred
            pred = sc.apply(spread_cal, market_code, "point", pred)

            qs = {}
            is_count = market_code in bp.COUNT_MARKETS
            if is_count:
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

            # Re-read the range at the levels that make it honest, measured
            # on the player-games books actually price. Before the median
            # correction below, so the median is corrected once and on the
            # corrected ladder.
            #
            # Not on the count markets, for two reasons. Their ladder is a
            # Poisson built to have the point projection as its mean, so the
            # quantiles and the point are consistent by construction and
            # moving one without the other is what put the board's median at
            # 1.00 against this page's 1.45 on the same Mahomes row. And the
            # correction was never fitted on a Poisson ladder in the first
            # place: the rows it learns from carry quantile-regression output.
            # The edge builder already skips it here; this did not, so pass_td
            # and rush_att would have shown a corrected range on the player
            # page and an uncorrected one on the board.
            if not is_count:
                qs = ic.apply(interval_cal, market_code, qs)

            # Same anchor the edge builder applies, in the same place, or the
            # two pages disagree on the number they both call the median.
            if qs.get(0.50) is not None:
                qs = dict(qs)
                qs[0.50] = ma.apply(anchor_cal, market_code, pred, qs[0.50],
                                    qs.get(0.25), qs.get(0.75))

            # The inherited level again, this time for the range. Scaling the
            # whole ladder by whatever the median needed keeps its shape, so
            # the spread still belongs to the player rather than being
            # flattened onto a point.
            if qs and vacated_vol.level(r.player_id, market_code) is not None:
                mid = qs.get(0.50)
                if mid and mid > 0:
                    qf = vacated_vol.factor(r.player_id, market_code, mid)
                    if qf != 1.0:
                        qs = {q: v * qf for q, v in qs.items()}

            p50_raw = qs.get(0.50)
            if qs.get(0.50) is not None:
                qs = dict(qs)
                qs[0.50] = sc.apply(spread_cal, market_code, "median", qs[0.50])
                # A corrected median must stay inside its own band.
                lo = qs.get(0.25)
                hi = qs.get(0.75)
                if lo is not None and qs[0.50] < lo:
                    qs[0.50] = lo
                if hi is not None and qs[0.50] > hi:
                    qs[0.50] = hi

            rows.append({
                "player_id": r.player_id, "player_name": r.player_name,
                "position": r.position, "team": r.team, "opponent": r.opponent,
                "game_id": r.game_id, "game_date": r.game_date,
                "market_code": market_code,
                "projection": pred,
                "projection_raw": pred_raw,
                "p50_raw": p50_raw,
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

        # Keep a copy that survives the next truncate.
        #
        # player_projections is the upcoming slate and nothing else, so the
        # moment a game is played the number we published for it is gone. The
        # only other record is prop_edge_results, which needs a posted line and
        # a pick that cleared the filters, so a player we projected but never
        # bet leaves no trace at all. One row per player, market and game, last
        # projection wins.
        conn.execute(text("""
            INSERT INTO player_projection_history (
                player_id, player_name, team, opponent, position, market_code,
                game_date, projection, projection_raw, p50_raw,
                p10, p25, p50, p75, p90, model_name,
                depth_rank, projected_at)
            SELECT player_id, player_name, team, opponent, position,
                   market_code, game_date, projection, projection_raw, p50_raw,
                   p10, p25, p50, p75, p90,
                   model_name, depth_rank, NOW()
            FROM player_projections
            ON CONFLICT (player_id, market_code, game_date) DO UPDATE SET
                player_name = EXCLUDED.player_name,
                team        = EXCLUDED.team,
                opponent    = EXCLUDED.opponent,
                position    = EXCLUDED.position,
                projection  = EXCLUDED.projection,
                projection_raw = EXCLUDED.projection_raw,
                p50_raw     = EXCLUDED.p50_raw,
                p10 = EXCLUDED.p10, p25 = EXCLUDED.p25, p50 = EXCLUDED.p50,
                p75 = EXCLUDED.p75, p90 = EXCLUDED.p90,
                model_name  = EXCLUDED.model_name,
                depth_rank  = EXCLUDED.depth_rank,
                projected_at = NOW()
        """))

    print(f"\nPROJECTIONS BUILT: {len(out)} rows")
    print(f"  players covered : {out['player_id'].nunique()}")
    print(f"  starters        : {out[out['is_starter']]['player_id'].nunique()}")
    if skipped:
        print(f"  skipped (no active model): {skipped} rows")
    if ruled_out:
        print(f"  not projected (ruled out or doubtful): {ruled_out} rows")
    print("\nby market:")
    print(out.groupby("market_code").agg(
        players=("player_id", "nunique"),
        mean_proj=("projection", "mean"),
    ).round(2).to_string())


if __name__ == "__main__":
    main()
