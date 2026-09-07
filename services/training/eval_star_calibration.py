"""Does the model lowball the players who actually score?

The anytime-touchdown board had no stars on it. The model's ranking was right,
so the stars were being excluded by the EV filter, which only happens if the
model's number sits below what the book is charging. Spot checks looked bad:
Jonathan Taylor projected at 0.560 against a 2025 rate of 1.176.

A per-player average is not what the model is predicting, though. It predicts a
specific game against a specific defense. So this asks the question the right
way round: split the held-out season by what the player had actually been doing
coming in, and check whether the model's number matches what happened next.

If the model is lowballing real scorers, the high-usage groups will show actual
well above predicted. If it is not, the stars are absent because the book is
charging more than they are worth, which is a finding rather than a fault.
"""

import json
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import eval as ev
import train as tr
from backtest_season import DATABASE_URL

MARKET = "any_td"
HOLDOUT = 2025


def main():
    eng = create_engine(DATABASE_URL, future=True)
    with eng.connect() as c:
        row = c.execute(text(
            "SELECT a.model_name, a.lookback FROM active_models a "
            "JOIN prop_markets m ON m.id=a.market_id WHERE m.code=:m"
        ), {"m": MARKET}).mappings().first()

    os.environ["MARKET_CODE"] = MARKET
    ev.MARKET_CODE = MARKET
    cols = json.load(open(os.path.join(
        ev.ARTIFACT_DIR,
        f"{row['model_name']}_{MARKET}_lb{row['lookback']}.json")))["feature_cols"]

    df = ev.load_labeled_rows(cols)
    d = pd.to_datetime(df["as_of_game_date"])
    yr = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
    train, test = df[yr < HOLDOUT].copy(), df[yr == HOLDOUT].copy()

    m = tr.build_model(row["model_name"])
    m.fit(ev.build_feature_matrix(train, cols), train[ev.LABEL_COL].astype(float))
    test["pred"] = np.clip(m.predict(ev.build_feature_matrix(test, cols)), 0, None)
    test["actual"] = test[ev.LABEL_COL].astype(float)

    # "mean" is the player's own trailing average over the lookback window: what
    # he had been doing coming into this game, known before kickoff.
    test["form"] = pd.to_numeric(test["mean"], errors="coerce")
    test = test.dropna(subset=["form"])

    print(f"holdout {HOLDOUT}: {len(test)} player-games\n")
    print("Split by the player's own trailing touchdown rate coming in:")
    print(f"{'trailing rate':<20}{'n':>7}{'predicted':>12}{'actual':>10}{'gap':>10}")
    bands = [(0.0, 0.05), (0.05, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.9), (0.9, 9.0)]
    for lo, hi in bands:
        s = (test["form"] >= lo) & (test["form"] < hi)
        if s.sum() < 40:
            continue
        p, a = test.loc[s, "pred"].mean(), test.loc[s, "actual"].mean()
        print(f"{f'{lo:.2f} to {hi:.2f}':<20}{int(s.sum()):>7}{p:>12.3f}"
              f"{a:>10.3f}{a - p:>+10.3f}")

    print("\nHow far the model is willing to go:")
    for q in (0.5, 0.9, 0.99, 1.0):
        print(f"  predicted quantile {q:<5} {test['pred'].quantile(q):.3f}")
    top = test.nlargest(400, "form")
    print(f"\nThe 400 highest-usage games in the holdout:")
    print(f"  predicted {top['pred'].mean():.3f}   actual {top['actual'].mean():.3f}"
          f"   gap {top['actual'].mean() - top['pred'].mean():+.3f}")


if __name__ == "__main__":
    main()
