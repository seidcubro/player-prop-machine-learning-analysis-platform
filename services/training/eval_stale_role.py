"""Does a measured stale-demotion adjustment survive out-of-sample testing?

A player whose rolling window is months old and who has since dropped down the
depth chart produces far less than his window suggests -- roughly 44% of his
weighted mean in-sample. The model cannot learn this: the bucket is ~0.3% of
rows, and with `max_features="sqrt"` the relevant feature is almost never even
considered at a split (measured importance 0.0002).

That leaves an explicit adjustment, which is only defensible if the factor is
*estimated on past data and shown to help on future data it never saw*. This
script does exactly that: fit the factor on the earlier portion, apply it to the
later portion, and report whether error improves. If it does not, the adjustment
should not ship.

The factor is shrunk toward 1.0 in proportion to sample size (empirical-Bayes
style), so a handful of observations cannot license a huge correction.
"""

import os

import numpy as np
import pandas as pd
import joblib

import eval as ev

STALE_DAYS = float(os.getenv("STALE_DAYS", "60"))
# Prior strength: with PRIOR_N comparable observations the factor is pulled
# halfway back to "no adjustment". Keeps a small sample from driving a big move.
PRIOR_N = float(os.getenv("PRIOR_N", "40"))


def main():
    meta = ev.load_model_metadata()
    cols = meta["feature_cols"]
    df = ev.load_labeled_rows(cols)

    ex = df["extra_features"].apply(ev._normalize_extra_features)
    df = df.assign(
        delta=ex.apply(lambda d: d.get("depth_rank_delta", 0.0)),
        stale=ex.apply(lambda d: d.get("days_since_last_game", 0.0)),
    )
    df = df.sort_values("as_of_game_date").reset_index(drop=True)

    cut = int(len(df) * 0.75)
    train, test = df.iloc[:cut], df.iloc[cut:]

    model = joblib.load(
        os.path.join(ev.ARTIFACT_DIR, os.path.basename(meta["artifact_path"]))
    )

    def predict(frame):
        return np.clip(model.predict(ev.build_feature_matrix(frame, cols)), 0.0, None)

    def bucket(frame):
        return (frame["delta"] >= 1) & (frame["stale"] > STALE_DAYS)

    tr_b = train[bucket(train)]
    if len(tr_b) < 10:
        print(f"only {len(tr_b)} training rows in bucket; not enough to estimate")
        return

    # Factor = how much of the model's own prediction actually materialises.
    tr_pred = predict(tr_b)
    tr_actual = tr_b[ev.LABEL_COL].astype(float).to_numpy()
    keep = tr_pred > 1e-6
    raw_factor = float(np.mean(tr_actual[keep] / tr_pred[keep]))

    n = len(tr_b)
    factor = (n * raw_factor + PRIOR_N * 1.0) / (n + PRIOR_N)

    print(f"\n=== {ev.MARKET_CODE} | stale>{STALE_DAYS:.0f}d and demoted ===")
    print(f"train rows in bucket: {n}")
    print(f"raw factor (in-sample):      {raw_factor:.3f}")
    print(f"shrunk factor (prior n={PRIOR_N:.0f}): {factor:.3f}")

    te_b = test[bucket(test)]
    if len(te_b) < 5:
        print(f"\nonly {len(te_b)} held-out rows in bucket -- cannot validate honestly")
        return

    te_pred = predict(te_b)
    te_actual = te_b[ev.LABEL_COL].astype(float).to_numpy()
    adj = te_pred * factor

    def mae(p):
        return float(np.mean(np.abs(p - te_actual)))

    def bias(p):
        return float(np.mean(p - te_actual))

    print(f"\nheld-out rows in bucket: {len(te_b)}")
    print(f"{'':<12}{'MAE':>9}{'bias':>9}")
    print(f"{'unadjusted':<12}{mae(te_pred):>9.2f}{bias(te_pred):>9.2f}")
    print(f"{'adjusted':<12}{mae(adj):>9.2f}{bias(adj):>9.2f}")

    better = mae(adj) < mae(te_pred)
    print(
        f"\nVERDICT: adjustment {'HELPS' if better else 'DOES NOT HELP'} out of sample "
        f"({mae(te_pred):.2f} -> {mae(adj):.2f} MAE)"
    )

    # Sanity: it must not wreck everything else.
    rest = test[~bucket(test)]
    print(f"(untouched rows outside the bucket: {len(rest)})")

    # Persist the factor only when it earns its place on held-out data, so a
    # market where the correction does not help simply has no entry and the
    # edge builder leaves its projections alone.
    if os.getenv("WRITE_FACTORS", "0") == "1":
        import json

        path = os.path.join(ev.ARTIFACT_DIR, "stale_role_factors.json")
        store = {}
        if os.path.exists(path):
            with open(path) as f:
                store = json.load(f)
        if better:
            store[ev.MARKET_CODE] = {
                "factor": round(factor, 4),
                "stale_days": STALE_DAYS,
                "train_rows": int(n),
                "holdout_rows": int(len(te_b)),
                "holdout_mae_before": round(mae(te_pred), 3),
                "holdout_mae_after": round(mae(adj), 3),
            }
            print(f"wrote factor for {ev.MARKET_CODE} -> {path}")
        else:
            store.pop(ev.MARKET_CODE, None)
            print(f"no factor stored for {ev.MARKET_CODE} (did not help)")
        with open(path, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
