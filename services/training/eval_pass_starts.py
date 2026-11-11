"""Are the passing models low because they are trained on games that were not starts?

diagnose_pass_volume.py ruled out the window. A quarterback whose last five
games were all clean starts is read just as low as everyone else (2.4 attempts,
2.6 completions, 23 yards), and the plain average of his last five starts is
not low at all (+0.6, +0.2, +0.2). So the inputs are fine and the model turns
them into a number that is too small.

The suspect is what the model is asked to predict. It is fitted on every
quarterback game in the features table, and that includes the relief
appearance, the game he left in the first quarter and the backup's four
kneel-downs. Its median is therefore "what a quarterback with this history
throws, counting the days he barely played". A prop is not that question. The
book prices him as the starter and the bet is void if he does not play, and
the record only grades games he threw ten or more passes. Ask a different
question and the answer comes out low by the size of the difference.

This fits the median model twice on the seasons before the holdout, with the
same features and the same settings as train_quantiles.py:

    as trained     every quarterback row
    starts only    rows where he threw START_ATTEMPTS or more in that game

and scores both on the holdout season's starts, which is what gets priced. If
"starts only" brings the median miss to about zero without a worse mean miss,
that is the cause, and the fix is the same filter in train.py and
train_quantiles.py followed by a retrain.

Run with no MARKET_CODE and it does the three passing markets in turn.

Read-only. Trains two small models per market and saves nothing.

    docker compose run --rm training python eval_pass_starts.py
"""

import os
import subprocess
import sys

MARKETS = ["pass_att", "pass_completions", "pass_yds"]
START = float(os.getenv("START_ATTEMPTS", "10"))


def one_market():
    import numpy as np
    import pandas as pd
    from sqlalchemy import create_engine, text

    import build_prop_edges as bp
    import eval as ev
    import train_quantiles as tq

    meta = tq.active_model_meta()
    cols = meta["feature_cols"]
    df = ev.load_labeled_rows(cols)
    keep = tq.PRICED_POSITIONS.get(ev.MARKET_CODE)
    if keep:
        df = df[df["position"].isin(keep)]

    eng = create_engine(bp.DATABASE_URL, future=True)
    att = pd.read_sql(text("""
        SELECT player_id, game_date AS as_of_game_date, attempts
        FROM player_game_stats_app WHERE attempts IS NOT NULL
    """), eng)
    att["as_of_game_date"] = pd.to_datetime(att["as_of_game_date"]).dt.date
    att = att.drop_duplicates(["player_id", "as_of_game_date"])
    df = df.merge(att, on=["player_id", "as_of_game_date"], how="left")
    matched = df["attempts"].notna().mean()
    df["start"] = pd.to_numeric(df["attempts"], errors="coerce").fillna(0) >= START

    train_df, test_df = ev.season_split(df)
    print(f"{ev.MARKET_CODE}: {matched:.0%} of rows matched to a game log. "
          f"{(~train_df['start']).mean():.0%} of the training rows are games "
          f"under {START:.0f} attempts.")
    if matched < 0.8:
        print("  too few matched to trust the split below.")

    test = test_df[test_df["start"]]
    priced = np.asarray(tq.priced_like_mask(test, ev.MARKET_CODE), dtype=bool)
    X_test = ev.build_feature_matrix(test, cols)
    y_test = test[ev.LABEL_COL].astype(float).to_numpy()

    print(f"  scored on {len(test)} holdout starts, {int(priced.sum())} of "
          f"them by quarterbacks the books price")
    print(f"  {'trained on':<14}{'rows':>7}{'median miss':>13}{'we were low':>13}"
          f"{'mean miss':>11}   the same on priced quarterbacks")
    for label, rows in (("as trained", train_df),
                        ("starts only", train_df[train_df["start"]])):
        m = tq.build_quantile_model(0.50)
        m.fit(ev.build_feature_matrix(rows, cols),
              rows[ev.LABEL_COL].astype(float))
        pred = np.clip(m.predict(X_test), 0.0, None)
        miss = y_test - pred
        out = (f"  {label:<14}{len(rows):>7}{np.median(miss):>+13.2f}"
               f"{(miss > 0).mean():>13.0%}{np.abs(miss).mean():>11.2f}")
        if priced.sum() >= 20:
            pm = miss[priced]
            out += (f"   {np.median(pm):+.2f}, low {(pm > 0).mean():.0%}, "
                    f"mean miss {np.abs(pm).mean():.2f}")
        print(out)
    print()


def main():
    if os.getenv("MARKET_CODE") in MARKETS and os.getenv("_PASS_STARTS_CHILD"):
        one_market()
        return
    for m in MARKETS:
        env = dict(os.environ, MARKET_CODE=m, SPLIT_MODE="season",
                   LOOKBACK=os.getenv("PASS_LOOKBACK", "8"),
                   _PASS_STARTS_CHILD="1")
        r = subprocess.run([sys.executable, __file__], env=env)
        if r.returncode:
            print(f"{m}: failed with exit {r.returncode}")
    print("median miss is actual minus the model, so positive means the model "
          "read low. The fix is worth making if starts only is near zero and "
          "its mean miss is no worse.")


if __name__ == "__main__":
    main()
