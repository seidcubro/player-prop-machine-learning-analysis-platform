"""Which step makes the published median read low?

The passing medians I publish sat 2.7 attempts, 2.1 completions and 19.5 yards
under the outcome through nine weeks of 2026. Two suspects are already cleared:

    the window    a quarterback with five clean starts behind him is read just
                  as low (diagnose_pass_volume.py).
    the training  the median model fitted on every game, scored on this
                  season's starts, misses by +0.15 attempts, +0.46 completions
                  and +3.0 yards. Fitted on starts only it is the same
                  (eval_pass_starts.py).

So the model's own median is right and the number on the board is not. Between
the two the builder applies a chain of corrections, each fitted separately and
each defensible on the day it was added:

    raw            the fitted 50th percentile
    season         this season's level, where it validated      season_bias.py
    map            re-read at the level that makes it honest     the bundle's
                   on past seasons                               calibration
    interval       the second-stage range correction             interval_calibration.py
    anchor         the median re-read off the point projection   median_anchor.py
    spread         the median's own spread correction            spread_calibration.py

This replays that chain on the props the record graded this season, one step
at a time, and prints how far each step moved the median and how far the
result then sat from the outcome. The step where "actual minus ours" jumps
away from zero is the one doing the damage.

It does this for every market with a fitted range, not just passing, because
receiving yards reads 4 low as well and may share the cause.

What it leaves out: the stale-role factor, the thin-sample anchor and inherited
volume. Those need the injury report and depth chart as they stood before each
game, and they touch a minority of rows. The last column is the median that
was actually published, so if the replayed chain lands near it, what was left
out does not matter.

The shipped models have seen this season's games, so the raw column is a
little flattered. That does not affect what each later step adds.

Read-only.

    docker compose run --rm training python diagnose_median_stages.py
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import build_prop_edges as bp
import eval as ev
import interval_calibration as interval
import median_anchor as anchor
import season_bias
import spread_calibration as spread

SEASON = int(os.getenv("SEASON", "2026"))
START = float(os.getenv("START_ATTEMPTS", "10"))
MARKETS = ["pass_att", "pass_completions", "pass_yds", "rec_yds", "recs",
           "rush_att", "rush_yds"]
PASSING = {"pass_att", "pass_completions", "pass_yds"}
STAGES = ["raw", "season", "map", "interval", "anchor", "spread"]


def predict(model, X):
    names = getattr(model, "feature_names_in_", None)
    if names is not None:
        X = X.reindex(columns=[str(c) for c in names], fill_value=0.0).astype(float)
    return np.asarray(model.predict(X), dtype=float)


def chain(qp, point, market, cals, calibration):
    """One row through the builder's steps. Returns the median after each."""
    spread_cal, interval_cal, anchor_cal, season_cal = cals
    out = {"raw": qp[0.50]}
    shift = season_bias.shift(season_cal, market)
    if shift:
        qp = {q: max(0.0, v + shift) for q, v in qp.items()}
    out["season"] = qp[0.50]
    cal_q = bp.calibrated_quantiles(qp, calibration)
    out["map"] = float(cal_q[0.50])
    cal_q = interval.apply(interval_cal, market, cal_q)
    out["interval"] = float(cal_q.get(0.50, out["map"]))
    med = anchor.apply(anchor_cal, market, point, out["interval"],
                       cal_q.get(0.25), cal_q.get(0.75))
    out["anchor"] = float(med)
    med = spread.apply(spread_cal, market, "median", float(med))
    lo, hi = cal_q.get(0.25), cal_q.get(0.75)
    if lo is not None and med < lo:
        med = float(lo)
    if hi is not None and med > hi:
        med = float(hi)
    out["spread"] = float(med)
    return out


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    art = Path(bp.ARTIFACT_DIR)
    cals = (spread.load(art), interval.load(art), anchor.load(art),
            season_bias.load(art))
    print()

    for market in MARKETS:
        loaded = bp.load_model_meta(art, market, eng)
        if not loaded:
            print(f"{market}: no active model, skipped")
            continue
        meta, model = loaded
        with eng.connect() as conn:
            lb = conn.execute(text("""
                SELECT a.lookback FROM active_models a
                JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c
            """), {"c": market}).scalar()
        quant = bp.load_quantile_bundle(art, market, int(lb))
        if quant is None:
            print(f"{market}: no fitted range, skipped")
            continue

        d = pd.read_sql(text("""
            WITH graded AS (
                SELECT player_id, game_date,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY line) AS line,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY projection_median)
                           AS published,
                       MAX(actual) AS actual
                FROM prop_edge_results
                WHERE source = 'live' AND market_code = :c
                  AND game_date >= :since AND actual IS NOT NULL
                  AND projection_median IS NOT NULL
                GROUP BY 1, 2
            )
            SELECT pmf.player_id, pmf.as_of_game_date, pmf.mean, pmf.stddev,
                   pmf.weighted_mean, pmf.trend, pmf.aux_mean, pmf.aux_trend,
                   pmf.extra_features, g.line, g.published, g.actual,
                   s.attempts
            FROM player_market_features pmf
            JOIN prop_markets pm ON pm.id = pmf.market_id
            JOIN graded g ON g.player_id = pmf.player_id
                         AND g.game_date = pmf.as_of_game_date
            LEFT JOIN player_game_stats_app s
                   ON s.player_id = pmf.player_id
                  AND s.game_date = pmf.as_of_game_date
            WHERE pm.code = :c AND pmf.lookback = :lb
        """), eng, params={"c": market, "lb": int(lb),
                           "since": f"{SEASON}-08-01"})
        d = d.drop_duplicates(["player_id", "as_of_game_date"])
        if market in PASSING:
            d = d[pd.to_numeric(d["attempts"], errors="coerce").fillna(0) >= START]
        if len(d) < 20:
            print(f"{market}: only {len(d)} graded props matched to a feature "
                  f"row, skipped")
            continue

        X = ev.build_feature_matrix(d, meta["feature_cols"])
        point = np.clip(predict(model, X), 0.0, None)
        ladder = {q: np.clip(predict(qm, X), 0.0, None)
                  for q, qm in quant["models"].items()}
        rows = [chain({q: float(v[i]) for q, v in ladder.items()},
                      float(point[i]), market, cals, quant.get("calibration"))
                for i in range(len(d))]
        s = pd.DataFrame(rows, index=d.index)
        actual = d["actual"].astype(float)

        print(f"{market.upper()}  {len(d)} graded props. Medians, in the "
              f"market's own units.")
        print(f"  {'step':<11}{'moved by':>10}{'actual - ours':>15}"
              f"{'we were low':>13}{'ours - line':>13}")
        prev = None
        for st in STAGES:
            moved = 0.0 if prev is None else float((s[st] - s[prev]).median())
            miss = actual - s[st]
            print(f"  {st:<11}{moved:>+10.2f}{miss.median():>+15.2f}"
                  f"{(miss > 0).mean():>13.0%}"
                  f"{(s[st] - d['line']).median():>+13.2f}")
            prev = st
        pub = actual - d["published"].astype(float)
        print(f"  {'published':<11}{'':>10}{pub.median():>+15.2f}"
              f"{(pub > 0).mean():>13.0%}"
              f"{(d['published'] - d['line']).median():>+13.2f}")
        print(f"  point projection: actual minus it "
              f"{(actual - point).median():+.2f}")
        cal = quant.get("calibration")
        if cal:
            lvl = float(np.interp(0.5, cal["level"], cal["grid"]))
            print(f"  the map reads the median at the raw {lvl:.3f} level "
                  f"(0.500 would leave it alone)")
        print()

    print("actual - ours above zero means the number read low. The step where "
          "it jumps is the one to fix. If the replayed 'spread' row is far "
          "from 'published', the steps left out (stale role, thin sample, "
          "inherited volume) carry the rest.")


if __name__ == "__main__":
    main()
