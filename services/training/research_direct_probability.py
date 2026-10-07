"""Model the probability the board selects on, instead of deriving it.

The pipeline today predicts the whole distribution of a stat and reads
P(stat > line) off the fitted CDF. That is elegant and it is indirect. The
board only ever asks one question of that distribution, at one point, and it is
the hardest point to be right about: the line is set where the density is
highest, so a large improvement in the fit shows up as almost no change at the
place the fit is used.

The tuning run makes that concrete. Across seventeen materially different
quantile models on receptions, pinball loss moved cleanly from 0.4085 to 0.4026
and the top expected-value decile never left 16.5% to 20.8%. The worst-fitting
candidate in the table had the best top decile. You can improve the
distribution and move no picks.

So: model P(over) directly. A binary classifier on "did this prop go over",
with the line as a feature, fitted only on rows a book actually priced.

Three reasons to expect it to do better, and they are not the same reason.

  **It optimises the quantity.** Log loss on the over/under outcome is a proper
  scoring rule for exactly the number the board subtracts the price from.
  Nothing is spent getting the tails right.

  **It can use the line.** The quantile models never see it. A line that sits
  high relative to a player's established role is itself evidence, and a
  regression has no way to express that: it predicts the stat and the line
  arrives afterwards. A classifier given both can learn the relationship.

  **It trains on the population it serves.** Priced props only, which is the
  population the board bets, rather than every player-game including the
  structural zeros that the EDA found make up half the rushing fit.

Two risks, stated before the numbers.

  **Fewer rows.** Priced props are roughly 4,000 a market against 20,000
  labelled player-games. A classifier with a quarter of the data and a harder
  target may simply be worse, and that is a real possibility rather than a
  caveat.

  **It will not be calibrated either.** Raw classifier output is not a
  probability you can subtract a price from. It needs the same isotonic
  treatment the current path gets, fitted on a season it did not see.

Scored the way tune.py scores: fitted on earlier rows, read on later ones it
never saw, reported on log loss and on the expected-value decile spread, which
is what the product collects. Ships nothing.

    MARKET_CODE=recs python research_direct_probability.py

Verdict, October 2026, receptions. It loses, and not narrowly.

                                      AUC   log loss    Brier   claimed   actual
    quantile ladder (incumbent)    0.5733     0.6882   0.2467     0.458    0.466
    direct classifier, raw         0.5273     0.7157   0.2594     0.451    0.466
    direct classifier, calibrated  0.5265     1.2582   0.3608     0.428    0.466

                                     top2     bot2   spread   picks
    quantile ladder (incumbent)    +24.9%    -2.0%   +26.9%     239
    direct classifier, calibrated  +12.7%    -3.6%   +16.2%     239

The incumbent wins on every measure. It orders the outcome far better, 0.5733
against 0.5265, and its top expected-value decile returns twice as much.

The isotonic step made things worse rather than better, which is its own
lesson: log loss goes from 0.716 to 1.258. Fitted on 1,858 rows an isotonic map
produces long flat steps and a few extreme ones, and an extreme probability
that is wrong costs far more under log loss than a timid one that is also
wrong. Calibration is not free on small samples.

The likeliest reason is simply data. The classifier sees 1,858 priced rows; the
ladder sees every labelled player-game before the same date, roughly ten times
as many. A regression learns what a player does from games nobody priced, and
then the line is applied afterwards. That turns out to be worth more than
seeing the line during training, at this sample size.

Which is the honest limit on this verdict. It refutes the idea *on one season
of odds history*, which is all there is. The experiment is kept because it is
worth re-running if the archive ever gets deeper: the argument for modelling the
quantity you select on has not been shown to be wrong, only to be outweighed by
having ten times the rows.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from psycopg2.extras import RealDictCursor
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

MARKET_CODE = os.getenv("MARKET_CODE", "recs")
LOOKBACK = int(os.getenv("LOOKBACK", "5"))
os.environ.setdefault("MARKET_CODE", MARKET_CODE)
os.environ.setdefault("LOOKBACK", str(LOOKBACK))

import train as tr  # noqa: E402
import train_quantiles as tq  # noqa: E402

HOLDOUT = int(os.getenv("HOLDOUT_SEASON", "2025"))
QUANTILES = tq.QUANTILES


def labelled():
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, eligible_positions FROM prop_markets WHERE code=%s",
                    (MARKET_CODE,))
        m = cur.fetchone()
        cur.execute("""
            SELECT pmf.player_id, p.name AS player_name, p.position,
                   pmf.as_of_game_date, pmf.mean, pmf.stddev, pmf.weighted_mean,
                   pmf.trend, pmf.aux_mean, pmf.aux_trend, pmf.extra_features,
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


def lines():
    key = tq.ODDS_MARKET_KEYS.get(MARKET_CODE)
    with tr.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT player_name, line, price_american, outcome_name,
                   (commence_time AT TIME ZONE 'America/New_York')::date AS d
            FROM odds_snapshots
            WHERE market_key = %s AND line IS NOT NULL
              AND price_american IS NOT NULL
        """, (key,))
        o = pd.DataFrame(cur.fetchall())
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


def ev_table(j, p_over, label):
    """ROI by decile of expected value, the way the board would collect it."""
    over = p_over >= 0.5
    price = np.where(over, j["over"], j["under"])
    p = np.where(over, p_over, 1.0 - p_over)
    pay = np.where(price > 0, price / 100.0, 100.0 / np.abs(price))
    ev = p - 1.0 / (1.0 + pay)
    won = np.where(over, j["label_actual"] > j["line"],
                   j["label_actual"] < j["line"])
    units = np.where(j["label_actual"] == j["line"], 0.0, np.where(won, pay, -1.0))
    q = pd.qcut(pd.Series(ev).rank(method="first"), 10, labels=False)
    lo = units[q <= 1].mean()
    hi = units[q >= 8].mean()
    print(f"  {label:<34}{hi:>+9.1%}{lo:>+9.1%}{hi - lo:>+9.1%}"
          f"{units[q >= 8].shape[0]:>8}")
    return hi, lo, hi - lo


def main():
    d = labelled()
    ln = lines()
    d["d"] = d["as_of_game_date"]
    priced = d.merge(ln, on=["player_name", "d"], how="inner")
    priced["went_over"] = (priced["label_actual"] > priced["line"]).astype(int)
    priced = priced[priced["label_actual"] != priced["line"]]
    print(f"{MARKET_CODE}: {len(d):,} labelled rows, {len(priced):,} priced with "
          f"an outcome; holdout {HOLDOUT}")

    # A chronological split inside the priced history, not a season holdout.
    #
    # The odds archive is one season: of 3,050 priced receptions props with an
    # outcome, 2,498 are 2025 and 243 predate it. Holding out 2025 leaves 243
    # rows to fit on, which answers nothing. So the cut is by date at
    # SPLIT_FRACTION through the priced rows, which keeps the split
    # walk-forward, the only property that matters, and puts the bulk of the
    # data where it does some good. Same constraint and same reasoning as
    # fit_interval_calibrator's.
    priced = priced.sort_values("d").reset_index(drop=True)
    cut = priced["d"].quantile(float(os.getenv("SPLIT_FRACTION", "0.60")))
    past = priced[priced["d"] <= cut]
    now = priced[priced["d"] > cut]
    print(f"  fit on priced rows to {cut.date()} ({len(past):,}), "
          f"read on {len(now):,} after\n")
    if len(past) < 400 or len(now) < 400:
        raise SystemExit(
            f"not enough priced history: {len(past)} before the cut and "
            f"{len(now)} after, from {len(priced)} priced rows in total. "
            f"The odds archive does not go back far enough for this market.")

    X_tr, cols = tr._build_feature_dataframe(past.copy())
    X_te, _ = tr._build_feature_dataframe(now.copy())
    for c in cols:
        if c not in X_te.columns:
            X_te[c] = 0.0
    X_tr, X_te = X_tr[cols].copy(), X_te[cols].copy()

    # The line, and the player's own level relative to it. A regression cannot
    # express either, because the line arrives after the prediction.
    for X, src in ((X_tr, past), (X_te, now)):
        X["line"] = src["line"].to_numpy(float)
        base = src["mean"].to_numpy(float)
        X["line_over_mean"] = base / np.where(src["line"] <= 0, np.nan,
                                              src["line"]).astype(float)
        X["line_minus_mean"] = base - src["line"].to_numpy(float)
    X_tr = X_tr.fillna(0.0)
    X_te = X_te.fillna(0.0)

    clf = HistGradientBoostingClassifier(
        max_iter=int(os.getenv("N_ESTIMATORS", "300")),
        learning_rate=float(os.getenv("LEARNING_RATE", "0.05")),
        max_depth=int(os.getenv("MAX_DEPTH", "3")),
        min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "30")),
        l2_regularization=float(os.getenv("L2_REG", "1.0")),
        random_state=42)
    clf.fit(X_tr, past["went_over"].to_numpy())
    raw = clf.predict_proba(X_te)[:, 1]

    # Calibrated on the training seasons, applied to the holdout, so the number
    # the expected value subtracts a price from is one that has been checked.
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
    iso.fit(clf.predict_proba(X_tr)[:, 1], past["went_over"].to_numpy())
    cal = iso.predict(raw)

    # The incumbent: the quantile ladder read at the line, fitted on every
    # labelled row before the same cut date. That is far more rows than the
    # classifier gets, and it is exactly the incumbent's real advantage: it does
    # not need a price to learn from a game.
    hist = d[d["d"] <= cut]
    Xq_tr, qcols = tr._build_feature_dataframe(hist.copy())
    Xq_te, _ = tr._build_feature_dataframe(now.copy())
    for c in qcols:
        if c not in Xq_te.columns:
            Xq_te[c] = 0.0
    yq = hist["label_actual"].astype(float).to_numpy()
    qp = {}
    for q in QUANTILES:
        m = tq.build_quantile_model(q)
        m.fit(Xq_tr[qcols], yq)
        qp[q] = np.maximum(0.0, m.predict(Xq_te[qcols]))
    p_over_q = 1.0 - np.clip(tq.cdf_level(qp, now["line"].to_numpy()), 0.01, 0.99)

    y = now["went_over"].to_numpy()
    print("  probability quality on the holdout\n")
    print(f"  {'':<34}{'AUC':>9}{'log loss':>11}{'Brier':>9}{'claimed':>10}{'actual':>9}")
    for name, p in (("quantile ladder (incumbent)", p_over_q),
                    ("direct classifier, raw", raw),
                    ("direct classifier, calibrated", cal)):
        print(f"  {name:<34}{roc_auc_score(y, p):>9.4f}"
              f"{log_loss(y, np.clip(p, 1e-6, 1 - 1e-6)):>11.4f}"
              f"{brier_score_loss(y, np.clip(p, 0, 1)):>9.4f}"
              f"{p.mean():>10.3f}{y.mean():>9.3f}")

    print("\n  what the board would collect\n")
    print(f"  {'':<34}{'top2':>9}{'bot2':>9}{'spread':>9}{'picks':>8}")
    ev_table(now, p_over_q, "quantile ladder (incumbent)")
    ev_table(now, cal, "direct classifier, calibrated")

    print("\n  AUC is the honest headline here: it says whether the number")
    print("  orders the over/under outcome at all, before any price is")
    print("  involved. The decile spread says whether that ordering survives")
    print("  contact with the book's prices, which is the only thing that pays.")


if __name__ == "__main__":
    main()
