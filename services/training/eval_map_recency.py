"""Is the calibration map a season out of date?

diagnose_median_stages.py found the step that makes the published medians read
low. The fitted median is right, within 0.1 to 2.2 of the outcome in every
market, and the bundle's calibration map then moves it: 15.5 yards down on
passing yards (it reads the median at the 0.416 level), 0.77 completions,
0.55 attempts, 1.3 receiving yards.

The map is measured one way (train_quantiles.fit_calibration_season_holdout):
fit the range on every season before the last complete one, see where that
season's outcomes fell on it, and ship the difference. So the map on the board
today says how a model that stopped at 2024 misjudged 2025. Production fell
between those seasons, the model read high, and the map learned to read
everything lower. This season the models do not read high, and the map is
still pulling them down by last year's amount.

That is a story, and this checks it the only way that counts. For each market
the range is fitted on seasons before this one and run on this season's games
by priced players. The season is cut by date. Four ways of reading the late
part:

    raw            the fitted range, no map
    last season    the map as it ships: measured on last season
    this season    the map measured on the early part of this season
    both           measured on last season and the early part together

scored on the late part, which none of them saw: how far the median sat from
the outcome, and how far all five published levels sat from what they claim
(the map exists for the tails as much as the middle, and a fix that mends the
median by breaking the p10 is not a fix).

If "this season" or "both" is closer on the median and no worse on the levels,
in most markets, the repair is to measure the map on the newest season that
has enough games, in train_quantiles.py, and retrain.

Read-only. Fits ten small models a market and saves nothing. Slow: allow ten
to twenty minutes.

    docker compose run --rm training python eval_map_recency.py
"""

import os
import subprocess
import sys

MARKETS = ["pass_att", "pass_completions", "pass_yds", "rec_yds", "recs",
           "rush_att", "rush_yds"]
EARLY = float(os.getenv("EARLY_FRACTION", "0.6"))


def apply_map(ladder, levels, cal):
    """Read each row's ladder at the levels the map says are honest."""
    import numpy as np
    if cal is None:
        return ladder
    grid = np.asarray(cal["grid"], dtype=float)
    mapped = np.asarray(cal["level"], dtype=float)
    out = np.empty_like(ladder)
    for j, t in enumerate(levels):
        raw_level = float(np.interp(t, mapped, grid))
        out[:, j] = [np.interp(raw_level, levels, row) for row in ladder]
    return out


def one_market():
    import numpy as np
    import pandas as pd

    import eval as ev
    import train_quantiles as tq

    meta = tq.active_model_meta()
    cols = meta["feature_cols"]
    df = ev.load_labeled_rows(cols)
    keep = tq.PRICED_POSITIONS.get(ev.MARKET_CODE)
    if keep:
        df = df[df["position"].isin(keep)]
    df = df.sort_values("as_of_game_date").reset_index(drop=True)
    season = tq.season_of(df["as_of_game_date"])
    priced = np.asarray(tq.priced_like_mask(df, ev.MARKET_CODE), dtype=bool)
    X = ev.build_feature_matrix(df, cols).to_numpy(dtype=float)
    y = df[ev.LABEL_COL].astype(float).to_numpy()
    levels = list(tq.QUANTILES)
    now = int(season.max())

    def probe(before, on):
        fit, use = season < before, (season == on) & priced
        if fit.sum() < 300 or use.sum() < 60:
            return None, use
        preds = {}
        for q in levels:
            m = tq.build_quantile_model(q)
            m.fit(X[fit], y[fit])
            preds[q] = np.clip(m.predict(X[use]), 0.0, None)
        return preds, use

    last, last_rows = probe(now - 1, now - 1)
    this, this_rows = probe(now, now)
    if this is None:
        print(f"{ev.MARKET_CODE}: too few priced rows this season, skipped\n")
        return
    n = int(this_rows.sum())
    cut = int(round(n * EARLY))
    y_now = y[this_rows]
    early = {q: v[:cut] for q, v in this.items()}
    late = np.sort(np.column_stack([this[q][cut:] for q in levels]), axis=1)
    y_early, y_late = y_now[:cut], y_now[cut:]
    if len(y_late) < 40:
        print(f"{ev.MARKET_CODE}: only {len(y_late)} late rows, skipped\n")
        return

    maps = {"raw": None,
            "this season": tq.fit_calibration(early, y_early)}
    if last is not None:
        y_last = y[last_rows]
        maps["last season"] = tq.fit_calibration(last, y_last)
        maps["both"] = tq.fit_calibration(
            {q: np.concatenate([last[q], early[q]]) for q in levels},
            np.concatenate([y_last, y_early]))

    print(f"{ev.MARKET_CODE.upper()}  range fitted before {now}. Maps measured "
          f"on {int(last_rows.sum()) if last is not None else 0} rows of "
          f"{now - 1} and the first {cut} of {now}; scored on the last "
          f"{len(y_late)}.")
    print(f"  {'read through':<14}{'median reads at':>16}{'actual - median':>17}"
          f"{'we were low':>13}{'levels off by':>15}   below p10 / p25 / p50 / p75 / p90")
    for name in ("raw", "last season", "this season", "both"):
        if name not in maps:
            continue
        cal = maps[name]
        read = apply_map(late, levels, cal)
        mid = read[:, levels.index(0.50)]
        miss = y_late - mid
        cov = [(y_late < read[:, j]).mean() for j in range(len(levels))]
        off = float(np.mean([abs(c - q) for c, q in zip(cov, levels)]))
        at = (0.5 if cal is None
              else float(np.interp(0.5, cal["level"], cal["grid"])))
        print(f"  {name:<14}{at:>16.3f}{np.median(miss):>+17.2f}"
              f"{(miss > 0).mean():>13.0%}{off:>15.3f}   "
              + " / ".join(f"{c:.2f}" for c in cov))
    print()


def main():
    if os.getenv("_MAP_RECENCY_CHILD"):
        one_market()
        return
    from sqlalchemy import create_engine, text

    import build_prop_edges as bp
    eng = create_engine(bp.DATABASE_URL, future=True)
    with eng.connect() as conn:
        lb = dict(conn.execute(text("""
            SELECT m.code, a.lookback FROM active_models a
            JOIN prop_markets m ON m.id = a.market_id
        """)).all())
    for m in MARKETS:
        if m not in lb:
            print(f"{m}: no active model, skipped")
            continue
        env = dict(os.environ, MARKET_CODE=m, SPLIT_MODE="season",
                   LOOKBACK=str(lb[m]), _MAP_RECENCY_CHILD="1")
        r = subprocess.run([sys.executable, __file__], env=env)
        if r.returncode:
            print(f"{m}: failed with exit {r.returncode}")
    print("actual - median above zero means the median read low. 'levels off "
          "by' is the mean distance of the five published levels from what "
          "they claim, so smaller is better and 0.000 is perfect. The row to "
          "beat is 'last season', which is what ships.")


if __name__ == "__main__":
    main()
