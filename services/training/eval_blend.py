"""Does the production blend + clamp actually help, or is it costing accuracy?

`build_prop_edges.py` does not serve the model's prediction. It serves
`0.3 * model + 0.7 * weighted_mean`, then clamps the result into
`weighted_mean +/- stddev`. That was added while chasing overprojection, but it
was never measured -- and if the model genuinely beats the rolling average
(eval.py says it does), then down-weighting it to 30% and clamping it back
toward the average throws away most of the gain.

This scores every candidate on the same held-out rows so the choice is made on
evidence rather than on which one feels safer.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
import joblib
import os

import eval as ev


def score(name, y, p):
    p = np.clip(np.asarray(p, dtype=float), 0.0, None)
    return {
        "variant": name,
        "r2": r2_score(y, p),
        "mae": mean_absolute_error(y, p),
        "rmse": float(np.sqrt(np.mean((p - y) ** 2))),
        "bias": float(np.mean(p - y)),
    }


def main():
    meta = ev.load_model_metadata()
    feature_cols = meta["feature_cols"]
    df = ev.load_labeled_rows(feature_cols)
    if ev.SPLIT_MODE == "season":
        _, test_df = ev.season_split(df)
    else:
        _, test_df = ev.time_split(df, ev.TEST_FRAC)

    X = ev.build_feature_matrix(test_df, feature_cols)
    model = joblib.load(
        os.path.join(ev.ARTIFACT_DIR, os.path.basename(meta["artifact_path"]))
    )

    y = test_df[ev.LABEL_COL].astype(float).to_numpy()
    m = np.clip(model.predict(X), 0.0, None)
    wm = test_df["weighted_mean"].astype(float).to_numpy()
    sd = test_df["stddev"].astype(float).to_numpy()

    rows = [
        score("weighted_mean only (naive baseline)", y, wm),
        score("model only", y, m),
    ]

    for w in (0.3, 0.5, 0.7, 0.85):
        rows.append(score(f"blend {w:.2f}*model + {1 - w:.2f}*wmean", y, w * m + (1 - w) * wm))

    # exactly what production does today
    blended = 0.3 * m + 0.7 * wm
    cap = np.minimum(sd, wm * 0.75)
    clamped = np.clip(blended, np.maximum(0.0, wm - cap), wm + cap)
    rows.append(score("PRODUCTION: 0.3 blend + clamp", y, clamped))

    # model alone, clamped
    rows.append(
        score("model only + clamp", y, np.clip(m, np.maximum(0.0, wm - cap), wm + cap))
    )

    out = pd.DataFrame(rows).sort_values("r2", ascending=False)
    print(f"\n=== {ev.MARKET_CODE} | split={ev.SPLIT_MODE} | rows={len(y)} ===")
    print(out.to_string(index=False, float_format=lambda v: f"{v:8.4f}"))


if __name__ == "__main__":
    main()
