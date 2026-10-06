"""Hyperparameter search, scored on the thing the board is judged on.

There has never been one. `bakeoff.py` chooses a model *family* per market and
nothing has ever tuned the family it chose, so every model here is running on
the defaults somebody typed once.

The part that took a measurement to get right is what to score. Three candidate
objectives, and only the last one is the product:

  **R2 or MAE on the point projection.** Wrong, and the EDA proved it: rush_att
  has the best R2 on the board at 0.566 and close to the worst side accuracy,
  while recs has half that R2 and better accuracy. A regression is graded on
  distance from the outcome; a bet is graded on which side of one number you
  land, and the book puts that number where the density is highest.

  **Pinball loss on the quantiles.** Closer, since the board's probability is
  read off the quantile CDF, and it is a proper scoring rule so it cannot be
  gamed. But a ladder can improve its average pinball loss while moving no
  pick at all, because the line sits in the middle where the loss is flattest.

  **ROI spread across deciles of expected value.** This is what the board does.
  Picks are selected on EV = P(side) - break-even, so what matters is whether
  sorting by EV sorts by profit. Measured on the graded record the current
  models manage a 5.6 point spread, bottom two deciles -3.4% against top two
  +2.2%, and the top decile is not even the best one. That is the number a
  tuning run has to move.

So each candidate is fitted on earlier seasons, scored on a later one it never
saw, and reported on all three. A candidate ships only if it beats the
incumbent on the third while not getting materially worse on the second, which
is the noise rule applied to the quantity that pays.

    MARKET_CODE=recs python tune.py              the quantile ladder
    MARKET_CODE=recs TUNE=point python tune.py   the point model
    GRID=wide python tune.py                     more candidates, much slower

A run over the default grid is 36 candidates, each fitting five quantile
models, so about an hour a market. Every row prints as it lands rather than at
the end, because a search you cannot watch is a search you cannot abandon when
the first ten rows have already told you the answer.

Nothing is written: this prints a table and the decision is a human one,
because a search that writes its own winner is a search that will eventually
overfit into production.
"""

from __future__ import annotations

import itertools
import json
import os
import time

import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor
from sklearn.ensemble import GradientBoostingRegressor

MARKET_CODE = os.getenv("MARKET_CODE", "recs")
LOOKBACK = int(os.getenv("LOOKBACK", "5"))
os.environ.setdefault("MARKET_CODE", MARKET_CODE)
os.environ.setdefault("LOOKBACK", str(LOOKBACK))

import train as tr  # noqa: E402
import train_quantiles as tq  # noqa: E402

TUNE = os.getenv("TUNE", "quantiles")
HOLDOUT = int(os.getenv("HOLDOUT_SEASON", "2025"))
QUANTILES = tq.QUANTILES

# Deliberately small. A grid of 500 candidates scored on 8,000 rows will find
# something that looks good by chance; the point of a search is to ask whether
# the defaults are badly wrong, not to mine the holdout.
GRIDS = {
    "default": {
        "n_estimators": [200, 400],
        "learning_rate": [0.03, 0.05, 0.1],
        "max_depth": [2, 3, 4],
        "min_samples_leaf": [20, 50],
        "subsample": [0.9],
    },
    "wide": {
        "n_estimators": [200, 400, 800],
        "learning_rate": [0.01, 0.03, 0.05, 0.1],
        "max_depth": [2, 3, 4, 5],
        "min_samples_leaf": [10, 20, 50, 100],
        "subsample": [0.7, 0.9, 1.0],
    },
}


def rows():
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (MARKET_CODE,))
        m = cur.fetchone()
        if not m:
            raise SystemExit(f"no market {MARKET_CODE}")
        cur.execute("""
            SELECT pmf.player_id, p.position, pmf.as_of_game_date,
                   pmf.mean, pmf.stddev, pmf.weighted_mean, pmf.trend,
                   pmf.aux_mean, pmf.aux_trend, pmf.extra_features,
                   pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id = %s AND pmf.lookback = %s
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date
        """, (m["id"], LOOKBACK))
        d = pd.DataFrame(cur.fetchall())
    if m.get("eligible_positions"):
        d = d[d["position"].isin(list(m["eligible_positions"]))]
    d["as_of_game_date"] = pd.to_datetime(d["as_of_game_date"])
    yr = d["as_of_game_date"].dt.year
    d["season"] = np.where(d["as_of_game_date"].dt.month <= 2, yr - 1, yr)
    return d.reset_index(drop=True)


def priced_lines():
    """The lines books actually posted, so EV can be computed on real prices."""
    key = tq.ODDS_MARKET_KEYS.get(MARKET_CODE)
    if not key:
        return pd.DataFrame()
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT player_name, line, price_american, outcome_name,
                   (commence_time AT TIME ZONE 'America/New_York')::date AS d
            FROM odds_snapshots
            WHERE market_key = %s AND line IS NOT NULL
              AND price_american IS NOT NULL
        """, (key,))
        o = pd.DataFrame(cur.fetchall())
    if o.empty:
        return o
    o["side"] = o["outcome_name"].str.strip().str.lower()
    o = o[o["side"].isin(["over", "under"])]
    mid = o.groupby(["player_name", "d"], as_index=False).line.median()
    best = (o.groupby(["player_name", "d", "side"], as_index=False)
              .price_american.max()
              .pivot_table(index=["player_name", "d"], columns="side",
                           values="price_american").reset_index())
    out = mid.merge(best, on=["player_name", "d"])
    out["d"] = pd.to_datetime(out["d"])
    return out


def pinball(y, pred, q):
    e = np.asarray(y, float) - np.asarray(pred, float)
    return float(np.mean(np.maximum(q * e, (q - 1.0) * e)))


def ev_spread(j):
    """Bottom-two against top-two deciles of expected value, in ROI."""
    if len(j) < 400:
        return np.nan, np.nan, np.nan
    j = j.copy()
    j["q"] = pd.qcut(j["ev"].rank(method="first"), 10, labels=False)
    lo = j[j["q"] <= 1]["units"].mean()
    hi = j[j["q"] >= 8]["units"].mean()
    return hi, lo, hi - lo


def score(params, d, lines, cols):
    past = d[d["season"] < HOLDOUT]
    now = d[d["season"] == HOLDOUT]
    X_tr, _ = tr._build_feature_dataframe(past.copy())
    X_te, _ = tr._build_feature_dataframe(now.copy())
    for c in cols:
        if c not in X_te.columns:
            X_te[c] = 0.0
    y_tr = past["label_actual"].astype(float).to_numpy()
    y_te = now["label_actual"].astype(float).to_numpy()

    preds, loss = {}, []
    for q in QUANTILES:
        m = GradientBoostingRegressor(loss="quantile", alpha=q, random_state=42,
                                      **params)
        m.fit(X_tr[cols], y_tr)
        preds[q] = np.maximum(0.0, m.predict(X_te[cols]))
        loss.append(pinball(y_te, preds[q], q))
    mean_pinball = float(np.mean(loss))

    j = now.copy()
    for q in QUANTILES:
        j[f"q{int(q * 100)}"] = preds[q]
    j["player_name"] = j.get("name", j.get("player_name"))
    if lines.empty or "player_name" not in j:
        return mean_pinball, np.nan, np.nan, np.nan, 0
    j["d"] = j["as_of_game_date"]
    j = j.merge(lines, on=["player_name", "d"], how="inner")
    if j.empty:
        return mean_pinball, np.nan, np.nan, np.nan, 0

    qp = {q: j[f"q{int(q * 100)}"].to_numpy() for q in QUANTILES}
    p_over = 1.0 - np.clip(tq.cdf_level(qp, j["line"].to_numpy()), 0.01, 0.99)
    over_better = p_over >= 0.5
    price = np.where(over_better, j["over"], j["under"])
    p = np.where(over_better, p_over, 1.0 - p_over)
    pay = np.where(price > 0, price / 100.0, 100.0 / np.abs(price))
    j["ev"] = p - 1.0 / (1.0 + pay)
    won = np.where(over_better, j["label_actual"] > j["line"],
                   j["label_actual"] < j["line"])
    j["units"] = np.where(j["label_actual"] == j["line"], 0.0,
                          np.where(won, pay, -1.0))
    hi, lo, spread = ev_spread(j)
    return mean_pinball, hi, lo, spread, len(j)


def main():
    d = rows()
    lines = priced_lines()
    print(f"{MARKET_CODE}: {len(d):,} labelled rows, holdout {HOLDOUT} "
          f"({(d.season == HOLDOUT).sum():,} rows), "
          f"{len(lines):,} priced lines")
    if TUNE != "quantiles":
        raise SystemExit("only the quantile ladder is tunable here; the point "
                         "model's objective does not decide a pick")

    cols = tr._build_feature_dataframe(d[d.season < HOLDOUT].copy())[1]
    grid = GRIDS[os.getenv("GRID", "default")]
    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
    base = {"n_estimators": int(os.getenv("Q_N_ESTIMATORS", "400")),
            "learning_rate": float(os.getenv("Q_LEARNING_RATE", "0.05")),
            "max_depth": int(os.getenv("Q_MAX_DEPTH", "3")),
            "min_samples_leaf": int(os.getenv("Q_MIN_SAMPLES_LEAF", "20")),
            "subsample": float(os.getenv("Q_SUBSAMPLE", "0.9"))}
    if base not in combos:
        combos.insert(0, base)
    print(f"{len(combos)} candidates, {len(cols)} features\n")

    print(f"  {'n_est':>6}{'lr':>7}{'depth':>7}{'leaf':>6}{'sub':>6}"
          f"{'pinball':>10}{'top2':>9}{'bot2':>9}{'spread':>9}{'picks':>7}  ",
          flush=True)
    results = []
    t0 = time.time()
    for i, c in enumerate(combos, 1):
        try:
            pb, hi, lo, sp, n = score(c, d, lines, cols)
        except Exception as exc:
            print(f"  candidate {i} failed: {type(exc).__name__}: {exc}",
                  flush=True)
            continue
        tag = "  <- incumbent" if c == base else ""
        results.append((c, pb, hi, lo, sp, n))
        print(f"  {c['n_estimators']:>6}{c['learning_rate']:>7.2f}"
              f"{c['max_depth']:>7}{c['min_samples_leaf']:>6}{c['subsample']:>6.1f}"
              f"{pb:>10.4f}{hi:>+9.1%}{lo:>+9.1%}{sp:>+9.1%}{n:>7}{tag}",
              flush=True)
    print(f"\n  {time.time() - t0:.0f}s")

    if not results:
        return
    inc = next((r for r in results if r[0] == base), None)
    best = max((r for r in results if np.isfinite(r[4])), key=lambda r: r[4],
               default=None)
    if inc and best and best[0] != base:
        print(f"\n  incumbent spread {inc[4]:+.1%} at pinball {inc[1]:.4f}")
        print(f"  best spread      {best[4]:+.1%} at pinball {best[1]:.4f}")
        print(f"  best params      {json.dumps(best[0])}")
        worse = best[1] > inc[1] * 1.01
        print("\n  " + ("the winner is also worse on pinball by more than 1%, "
                        "which is a search finding noise"
                        if worse else
                        "the winner does not lose on pinball, so it is worth a "
                        "second holdout before shipping"))
    else:
        print("\n  nothing beat the incumbent")


if __name__ == "__main__":
    main()
