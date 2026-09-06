"""Train a quantile ensemble per market, so win probability comes from a real
predicted distribution instead of an assumed one.

Why this exists
---------------
`build_prop_edges.py` used to turn a point projection into a win probability by
assuming the outcome is Gaussian around it, with sigma taken from the player's
rolling `stddev` and then hand-capped at `0.75 *` the line. That produces
absurd confidence -- 95% on a rushing-yards prop -- because a single capped
number cannot describe a distribution that is right-skewed, floored at zero, and
much wider for a workhorse than for a rotational player.

Instead, fit the conditional quantiles directly. Predicting q10..q90 gives an
empirical CDF per player-game, and P(over) is read straight off it. The spread
between quantiles *is* the uncertainty, learned per row from the features rather
than assumed, so a volatile boom/bust receiver naturally gets a wider interval
than a steady possession back.

Pinball (quantile) loss is the proper scoring rule for each fitted quantile, and
is reported per quantile so a bad fit is visible rather than silent.

Env: MARKET_CODE, LOOKBACK, QUANT_MODEL_NAME (default "quant_v1"),
     SPLIT_MODE (frac|season), plus the shared DB/artifact vars from eval.py.
"""

import json
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

import eval as ev

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
QUANT_MODEL_NAME = os.getenv("QUANT_MODEL_NAME", "quant_v1")


def pinball_loss(y_true, y_pred, q: float) -> float:
    """Proper scoring rule for a single quantile (lower is better)."""
    d = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return float(np.mean(np.maximum(q * d, (q - 1.0) * d)))


def build_quantile_model(q: float):
    return GradientBoostingRegressor(
        loss="quantile",
        alpha=q,
        n_estimators=int(os.getenv("Q_N_ESTIMATORS", "400")),
        learning_rate=float(os.getenv("Q_LEARNING_RATE", "0.05")),
        max_depth=int(os.getenv("Q_MAX_DEPTH", "3")),
        min_samples_leaf=int(os.getenv("Q_MIN_SAMPLES_LEAF", "20")),
        subsample=float(os.getenv("Q_SUBSAMPLE", "0.9")),
        random_state=42,
    )


def prob_over(qpreds: dict[float, np.ndarray], line: np.ndarray) -> np.ndarray:
    """P(outcome > line) read off the predicted quantile CDF.

    The fitted quantiles give CDF points (q_value, q_level). Interpolating the
    level at `line` gives P(outcome <= line) directly; complement for the over.
    Quantiles are sorted per row first because separately-fitted quantile models
    can cross, which would otherwise make the CDF non-monotonic.
    """
    levels = np.array(sorted(qpreds.keys()), dtype=float)
    mat = np.column_stack([qpreds[q] for q in sorted(qpreds.keys())])
    mat = np.sort(mat, axis=1)  # enforce monotone CDF per row

    out = np.empty(len(line), dtype=float)
    for i in range(len(line)):
        out[i] = 1.0 - float(np.interp(line[i], mat[i], levels))
    return np.clip(out, 0.01, 0.99)


def active_model_meta():
    """Resolve the market's *active* point model and load its metadata.

    Quantile models must live in exactly the feature space of the point model
    they accompany, or `build_prop_edges.py` will hand them a vector they were
    never fitted on. Deriving that from `active_models` rather than a MODEL_NAME
    env var removes the failure where an unset variable silently falls back to a
    legacy model with a much smaller feature set.
    """
    import json as _json

    from sqlalchemy import create_engine, text as _text

    url = os.getenv("DATABASE_URL") or (
        f"postgresql://{os.getenv('POSTGRES_USER', 'app')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'app')}@"
        f"{os.getenv('POSTGRES_HOST', 'postgres')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'app')}"
    )
    with create_engine(url, future=True).connect() as conn:
        row = conn.execute(
            _text(
                "SELECT a.model_name, a.lookback FROM active_models a "
                "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
            ),
            {"c": ev.MARKET_CODE},
        ).mappings().first()

    if not row:
        raise SystemExit(f"no active model for {ev.MARKET_CODE}; train one first")

    path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{ev.MARKET_CODE}_lb{row['lookback']}.json"
    )
    if not os.path.exists(path):
        raise SystemExit(f"active model metadata missing: {path}")

    with open(path) as f:
        meta = _json.load(f)
    print(f"using active point model {row['model_name']} "
          f"({len(meta['feature_cols'])} features)")
    return meta


def main():
    meta = active_model_meta()
    feature_cols = meta["feature_cols"]

    df = ev.load_labeled_rows(feature_cols)
    if ev.SPLIT_MODE == "season":
        train_df, test_df = ev.season_split(df)
    else:
        train_df, test_df = ev.time_split(df, ev.TEST_FRAC)

    X_train = ev.build_feature_matrix(train_df, feature_cols)
    X_test = ev.build_feature_matrix(test_df, feature_cols)
    y_train = train_df[ev.LABEL_COL].astype(float)
    y_test = test_df[ev.LABEL_COL].astype(float).to_numpy()

    models = {}
    losses = {}
    qpreds = {}
    for q in QUANTILES:
        m = build_quantile_model(q)
        m.fit(X_train, y_train)
        pred = np.clip(m.predict(X_test), 0.0, None)
        models[q] = m
        qpreds[q] = pred
        losses[f"q{int(q * 100)}"] = pinball_loss(y_test, pred, q)
        print(f"  q{int(q * 100):02d}  pinball={losses[f'q{int(q * 100)}']:.4f}")

    # Coverage: the share of actuals falling below each fitted quantile should
    # land near the quantile level itself. This is the honest check that the
    # intervals mean what they claim.
    coverage = {
        f"q{int(q * 100)}": float(np.mean(y_test <= qpreds[q])) for q in QUANTILES
    }
    print("\ncoverage (target = the quantile level itself):")
    for q in QUANTILES:
        k = f"q{int(q * 100)}"
        print(f"  {k}: {coverage[k]:.3f}  (target {q:.2f})")

    band = float(np.mean((y_test >= qpreds[0.10]) & (y_test <= qpreds[0.90])))
    print(f"\n80% interval actually contains {band:.1%} of outcomes (target 80%)")

    os.makedirs(ev.ARTIFACT_DIR, exist_ok=True)
    stem = f"{QUANT_MODEL_NAME}_{ev.MARKET_CODE}_lb{ev.LOOKBACK}"
    artifact_path = os.path.join(ev.ARTIFACT_DIR, f"{stem}.joblib")
    joblib.dump({"quantiles": QUANTILES, "models": models}, artifact_path)

    report = {
        "model_name": QUANT_MODEL_NAME,
        "market_code": ev.MARKET_CODE,
        "lookback": ev.LOOKBACK,
        "generated_at": date.today().isoformat(),
        "split_mode": ev.SPLIT_MODE,
        "artifact_path": artifact_path,
        "feature_cols": feature_cols,
        "point_model_used_for_features": meta["model_name"],
        "quantiles": QUANTILES,
        "pinball_loss": losses,
        "coverage": coverage,
        "interval_80_coverage": band,
        "rows_train": int(len(train_df)),
        "rows_test": int(len(test_df)),
    }
    with open(os.path.join(ev.ARTIFACT_DIR, f"{stem}.json"), "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nWrote {artifact_path}")


if __name__ == "__main__":
    main()
