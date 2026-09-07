"""Does the model compress everyone toward the middle?

The anytime-touchdown board came back with no stars on it. The model's ranking
was right, so the question was why every star failed the EV filter. Spot checks
pointed at shrinkage: Jonathan Taylor projected at 0.560 against an actual 2025
rate of 1.176, Kenny Gainwell projected at 0.533 against a career 0.197.

A model that pulls high-rate players down and pushes low-rate players up will
produce exactly the board we got. It will never bet a star, because it thinks
the book is asking too much, and it will happily bet a backup at +800, because
it thinks the backup is better than he is.

This measures it directly. Fit on seasons before the holdout, predict the
holdout, then regress actual on predicted. A slope of 1 means the spread is
right. A slope above 1 means the predictions are too tightly bunched: reality
spreads out further than the model is willing to.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import eval as ev
import train as tr
from backtest_season import DATABASE_URL


def season_of(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def run(market: str, holdout: int = 2025) -> dict | None:
    eng = create_engine(DATABASE_URL, future=True)
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id=a.market_id WHERE m.code=:m"
        ), {"m": market}).mappings().first()
    if not row:
        return None

    os.environ["MARKET_CODE"] = market
    ev.MARKET_CODE = market
    path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{market}_lb{row['lookback']}.json")
    if not os.path.exists(path):
        return None
    cols = json.load(open(path))["feature_cols"]

    df = ev.load_labeled_rows(cols)
    yr = season_of(df["as_of_game_date"])
    train, test = df[yr < holdout], df[yr == holdout]
    if len(test) < 300 or len(train) < 500:
        return None

    m = tr.build_model(row["model_name"])
    m.fit(ev.build_feature_matrix(train, cols), train[ev.LABEL_COL].astype(float))
    pred = np.asarray(m.predict(ev.build_feature_matrix(test, cols)), dtype=float)
    actual = test[ev.LABEL_COL].astype(float).to_numpy()

    # Regress actual on predicted. The slope is the whole answer: it is how much
    # reality moves for each unit the model moves.
    slope, intercept = np.polyfit(pred, actual, 1)
    return {"market": market, "n": len(test), "slope": slope,
            "intercept": intercept, "pred": pred, "actual": actual,
            "sd_pred": pred.std(), "sd_actual": actual.std()}


def main():
    markets = sys.argv[1:] or [
        "any_td", "rec_yds", "recs", "rush_yds", "rush_att",
        "pass_yds", "pass_att", "pass_completions", "pass_td"]

    print("Regression of actual on predicted, fitted before 2025, tested on 2025")
    print("slope 1.00 = spread is right; above 1.00 = predictions too bunched\n")
    print(f"{'market':<18}{'n':>7}{'slope':>8}{'sd pred':>10}{'sd actual':>11}"
          f"{'ratio':>8}")
    results = []
    for mk in markets:
        r = run(mk)
        if not r:
            print(f"{mk:<18}{'skipped':>7}")
            continue
        results.append(r)
        print(f"{r['market']:<18}{r['n']:>7}{r['slope']:>8.2f}"
              f"{r['sd_pred']:>10.3f}{r['sd_actual']:>11.3f}"
              f"{r['sd_actual'] / r['sd_pred']:>8.2f}")

    for r in results:
        print(f"\n=== {r['market']}: by predicted decile ===")
        print(f"{'decile':<10}{'n':>7}{'predicted':>12}{'actual':>10}{'gap':>10}")
        q = pd.qcut(r["pred"], 10, labels=False, duplicates="drop")
        for d in sorted(pd.unique(q)):
            s = q == d
            p, a = r["pred"][s].mean(), r["actual"][s].mean()
            print(f"{int(d) + 1:<10}{int(s.sum()):>7}{p:>12.3f}{a:>10.3f}"
                  f"{a - p:>+10.3f}")


if __name__ == "__main__":
    main()
