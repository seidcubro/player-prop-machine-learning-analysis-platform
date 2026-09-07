"""Why the predicted distributions are wrong, and whether the population is why.

The held-out backtest printed a calibration table that should not exist in a
working system. A calibrated q10 has the outcome below it 10% of the time. The
production bundles report:

    rush_att   q10 0.550   q25 0.550   q50 0.550
    rush_yds   q10 0.602   q25 0.642   q50 0.733
    rec_yds    q10 0.318   q25 0.383   q50 0.564
    recs       q10 0.288   q25 0.369   q50 0.562

Three identical numbers for rush_att is not miscalibration, it is degeneracy:
q10, q25 and q50 are all predicting the same thing. And 0.550 is not a
coincidence either. `rush_att` is eligible for QB, RB, WR and FB, and 53% of
those rows are structural zeros -- 8,502 WR rows that are 86.7% zero, plus the
fullbacks. The true conditional 10th, 25th and 50th percentiles for half the
training population are all exactly zero, the quantile loss is flat across that
whole region, and the fitted models collapse onto it. What is left over for an
actual running back is a median pinned far below where he really lands: on the
props that a book priced, only 4% of outcomes fell below the model's own q50.

This is the same zero-inflation the eligible-positions filter was added to
prevent, except the rush markets' list is permissive enough to reintroduce it.
`eval.load_labeled_rows` even documents the failure mode in its own docstring.

So: measure it rather than assume it. Fit the quantile ensemble twice per
market, once on the shipped population and once restricted to the positions
that books actually price, and print the coverage of both. Nothing is written
and nothing is changed; this only establishes whether the population is the
cause before anything is done about it.

Env: MARKETS (comma-separated, default the rush and receiving markets),
     SEASON (holdout season, default 2025).
"""

import os

import numpy as np
import pandas as pd

import eval as ev
import train_quantiles as tq

MARKETS = os.getenv("MARKETS", "rush_att,rush_yds,rec_yds,recs").split(",")
SEASON = int(os.getenv("SEASON", "2025"))

# The positions a sportsbook actually posts a line for. A wide receiver's
# rushing attempts are never priced, so their only effect on the fit is to
# drown the lower quantiles in zeros.
PRICED_POSITIONS = {
    "rush_att": ["QB", "RB"],
    "rush_yds": ["QB", "RB"],
    "rush_td": ["QB", "RB"],
    "rec_yds": ["WR", "TE", "RB"],
    "recs": ["WR", "TE", "RB"],
    "rec_td": ["WR", "TE", "RB"],
}


def coverage(models, X, y):
    cov = {}
    for q in tq.QUANTILES:
        pred = np.clip(models[q].predict(X), 0, None)
        cov[q] = float(np.mean(np.asarray(y, dtype=float) <= pred))
    return cov


def fit(train, cols):
    X = ev.build_feature_matrix(train, cols)
    y = train[ev.LABEL_COL].astype(float)
    out = {}
    for q in tq.QUANTILES:
        m = tq.build_quantile_model(q)
        m.fit(X, y)
        out[q] = m
    return out


def fmt(cov):
    return "".join(f"{cov[q]:>9.3f}" for q in tq.QUANTILES)


def main():
    import json

    from sqlalchemy import create_engine, text

    from backtest_season import DATABASE_URL

    engine = create_engine(DATABASE_URL, future=True)
    hdr = f"{'':<34}" + "".join(f"{'q' + str(int(q * 100)):>9}"
                                for q in tq.QUANTILES)

    for market in MARKETS:
        market = market.strip()
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market
        with engine.connect() as c:
            row = c.execute(text(
                "SELECT a.model_name, a.lookback FROM active_models a "
                "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
            ), {"c": market}).mappings().first()
        if not row:
            print(f"{market}: no active model")
            continue
        meta = os.path.join(
            ev.ARTIFACT_DIR,
            f"{row['model_name']}_{market}_lb{row['lookback']}.json")
        if not os.path.exists(meta):
            print(f"{market}: no metadata at {meta}")
            continue
        cols = json.load(open(meta))["feature_cols"]

        df = ev.load_labeled_rows(cols)
        dates = pd.to_datetime(df["as_of_game_date"])
        yr = dates.dt.year.where(dates.dt.month >= 3, dates.dt.year - 1)
        train_all, test_all = df[yr < SEASON], df[yr == SEASON]
        if len(train_all) < 300 or test_all.empty:
            print(f"{market}: not enough data")
            continue

        keep = PRICED_POSITIONS.get(market)
        train_cut = train_all[train_all["position"].isin(keep)] if keep else train_all
        test_cut = test_all[test_all["position"].isin(keep)] if keep else test_all

        zero_all = float((train_all[ev.LABEL_COL] == 0).mean())
        zero_cut = float((train_cut[ev.LABEL_COL] == 0).mean())

        print()
        print(f"=== {market} ===")
        print(f"shipped population  {len(train_all):>6} train rows, "
              f"{zero_all:.1%} of them exactly zero")
        print(f"priced positions    {len(train_cut):>6} train rows, "
              f"{zero_cut:.1%} of them exactly zero  {keep}")
        print(hdr)

        Xtest_cut = ev.build_feature_matrix(test_cut, cols)
        ytest_cut = test_cut[ev.LABEL_COL].astype(float)

        m_all = fit(train_all, cols)
        # Both are scored on the SAME rows, the priced positions in the holdout
        # season, or the comparison would be between two different questions.
        print(f"{'fit on shipped population':<34}"
              f"{fmt(coverage(m_all, Xtest_cut, ytest_cut))}")

        if keep and len(train_cut) >= 300:
            m_cut = fit(train_cut, cols)
            print(f"{'fit on priced positions only':<34}"
                  f"{fmt(coverage(m_cut, Xtest_cut, ytest_cut))}")
        print(f"{'target':<34}" + "".join(f"{q:>9.2f}" for q in tq.QUANTILES))


if __name__ == "__main__":
    main()
