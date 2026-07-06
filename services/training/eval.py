"""Evaluation script for market-specific projection models.

This module evaluates an already-trained model artifact against labeled feature
rows in Postgres and writes a structured JSON evaluation report.

Why this exists:
- Training metrics alone are not enough to trust a model for real usage.
- We need deterministic evaluation that mirrors production usage:
  - Time-aware split (train on earlier dates, test on later dates)
  - Breakdown metrics (by position, by label bucket)
  - Bias checks (systematic over/under)

Inputs:
- Reads labeled rows from player_market_features joined to prop_markets and players.
- Loads the trained model artifact (.joblib) from ARTIFACT_DIR.

Outputs:
- Writes eval report JSON to:
  /artifacts/evals/{model_name}_{market_code}_lb{lookback}_eval.json

Environment variables:
- POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
- MARKET_CODE (e.g., rec_yds)
- LOOKBACK (e.g., 5)
- MODEL_NAME (e.g., ridge_v1)
- ARTIFACT_DIR (default /artifacts)
- TEST_FRAC (default 0.20)  # final portion of time-ordered rows used for test
"""

import os
import json
import math
from datetime import date
from typing import Any
import joblib
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "app")
DB_USER = os.getenv("POSTGRES_USER", "app")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "app")

MARKET_CODE = os.getenv("MARKET_CODE", "rec_yds")
LOOKBACK = int(os.getenv("LOOKBACK", "5"))
MODEL_NAME = os.getenv("MODEL_NAME", "ridge_v1")
ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")
TEST_FRAC = float(os.getenv("TEST_FRAC", "0.20"))

DEFAULT_FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend"]
BASE_FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend", "aux_mean", "aux_trend"]
LABEL_COL = "label_actual"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except Exception:
        return default


def _normalize_extra_features(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return {}
        try:
            raw = json.loads(s)
        except Exception:
            return {}
    else:
        return {}
    return {str(k): _safe_float(v, 0.0) for k, v in raw.items()}


def build_feature_matrix(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Mirror train.py's _build_feature_dataframe so eval uses the exact same
    feature construction (base cols + expanded extra_features JSON + the
    target_share derived feature) the model was actually trained on.
    """
    work = df.copy()
    for c in BASE_FEATURE_COLS:
        if c not in work.columns:
            work[c] = 0.0
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)

    if "extra_features" not in work.columns:
        work["extra_features"] = None
    extras_series = work["extra_features"].apply(_normalize_extra_features)
    extra_keys = sorted({k for d in extras_series for k in d.keys()})
    extra_df = pd.DataFrame(
        [{k: d.get(k, 0.0) for k in extra_keys} for d in extras_series],
        index=work.index,
    )
    if not extra_df.empty:
        extra_df = extra_df.fillna(0.0).astype(float)

    X = work[BASE_FEATURE_COLS].copy()
    if not extra_df.empty:
        X = pd.concat([X, extra_df], axis=1)

    if "targets_mean" in X.columns and "team_pass_attempts" in X.columns:
        denom = pd.to_numeric(X["team_pass_attempts"], errors="coerce").fillna(0.0)
        numer = pd.to_numeric(X["targets_mean"], errors="coerce").fillna(0.0)
        target_share = numer / denom.replace(0, pd.NA)
        X["target_share"] = (
            pd.to_numeric(target_share, errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
        )

    X = X.loc[:, ~X.columns.duplicated()]
    return X.reindex(columns=feature_cols, fill_value=0.0).astype(float)


def connect():
    """Open a psycopg2 connection to Postgres using env vars."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )


def load_model_metadata() -> dict:
    """Load the model metadata JSON if available (feature_cols, target_transform).

    This prevents silent schema/target drift: evaluation must reproduce the
    exact feature construction and target transform used at training time.
    """
    meta_path = os.path.join(ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.json")
    if not os.path.exists(meta_path):
        return {"feature_cols": DEFAULT_FEATURE_COLS, "target_transform": "none"}

    with open(meta_path, "r") as f:
        meta = json.load(f)

    cols = meta.get("feature_cols")
    if not cols or not isinstance(cols, list):
        meta["feature_cols"] = DEFAULT_FEATURE_COLS
    meta.setdefault("target_transform", "none")
    return meta


def load_labeled_rows(feature_cols: list[str]) -> pd.DataFrame:
    """Load labeled feature rows for a market/lookback.

    Returns a DataFrame containing:
      - player_id
      - as_of_game_date
      - position
      - feature cols
      - label_actual

    Note:
      Every market restricts evaluation to prop_markets.eligible_positions
      (same population train.py trains on). Without this, markets with no
      eligible_positions filter get flooded with rows for positions that
      trivially never touch that stat (e.g. defensive linemen always
      rushing for 0 yards), which mechanically inflates R2 and shrinks
      MAE/RMSE -- making cross-market quality comparisons meaningless.
    """
    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT eligible_positions FROM prop_markets WHERE code = %s",
            (MARKET_CODE,),
        )
        market_row = cur.fetchone()
        eligible_positions = (market_row or {}).get("eligible_positions")

        cur.execute(
            """
            SELECT
              pmf.player_id,
              pmf.as_of_game_date,
              p.position,
              pmf.mean,
              pmf.stddev,
              pmf.weighted_mean,
              pmf.trend,
              pmf.aux_mean,
              pmf.aux_trend,
              pmf.extra_features,
              pmf.label_actual
            FROM player_market_features pmf
            JOIN prop_markets pm ON pm.id = pmf.market_id
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pm.code = %s
              AND pmf.lookback = %s
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date ASC, pmf.player_id ASC
            """,
            (MARKET_CODE, LOOKBACK),
        )
        rows = cur.fetchall()

    if not rows:
        raise SystemExit("No labeled rows found for evaluation.")

    df = pd.DataFrame(rows)
    df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"]).dt.date

    # Market-specific evaluation population filter (prevents zero-inflation cheating).
    if eligible_positions:
        df = df[df["position"].isin(eligible_positions)]

    # Enforce column presence (raw columns fetched from the DB; the model's
    # actual feature_cols are derived from these via build_feature_matrix,
    # so they are not expected to already exist as literal df columns).
    raw_cols = BASE_FEATURE_COLS + ["extra_features", LABEL_COL]
    missing = [c for c in raw_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing expected columns in evaluation query: {missing}")

    return df

def time_split(df: pd.DataFrame, test_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split rows by time order: earlier rows train, later rows test."""
    if test_frac <= 0.0 or test_frac >= 0.8:
        raise SystemExit("TEST_FRAC must be > 0 and < 0.8 (recommended 0.2).")

    n = len(df)
    if n < 200:
        # still allow eval, but be honest it's small
        pass

    cutoff = int(n * (1.0 - test_frac))
    cutoff = max(1, min(cutoff, n - 1))

    train_df = df.iloc[:cutoff].copy()
    test_df = df.iloc[cutoff:].copy()
    return train_df, test_df


def compute_metrics(y_true, y_pred) -> dict:
    """Compute standard regression metrics + bias."""
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(mean_squared_error(y_true, y_pred, squared=False))
    r2 = float(r2_score(y_true, y_pred))
    err = (y_pred - y_true)
    bias = float(err.mean())  # >0 = tends to overpredict, <0 = underpredict
    return {"mae": mae, "rmse": rmse, "r2": r2, "bias": bias}


def bucket_metrics(df: pd.DataFrame, y_col: str, pred_col: str) -> list[dict]:
    """Compute MAE by label magnitude buckets."""
    # bins tuned for yards-like stats; still works for many markets
    bins = [-1, 0, 10, 25, 50, 75, 100, 150, 10000]
    labels = ["0", "1-10", "11-25", "26-50", "51-75", "76-100", "101-150", "150+"]

    s = df[y_col].astype(float)
    df = df.copy()
    df["label_bucket"] = pd.cut(s, bins=bins, labels=labels)

    out = []
    for b in labels:
        sub = df[df["label_bucket"] == b]
        if len(sub) < 50:
            continue
        m = compute_metrics(sub[y_col].astype(float), sub[pred_col].astype(float))
        out.append({"bucket": b, "rows": int(len(sub)), **m})
    return out


def position_metrics(df: pd.DataFrame, y_col: str, pred_col: str) -> list[dict]:
    """Compute MAE by position (WR/TE/RB/etc)."""
    out = []
    for pos, sub in df.groupby("position"):
        if pos is None:
            pos = ""
        if len(sub) < 200:
            continue
        m = compute_metrics(sub[y_col].astype(float), sub[pred_col].astype(float))
        out.append({"position": str(pos), "rows": int(len(sub)), **m})
    out.sort(key=lambda x: x["mae"])
    return out


def main():
    """Run evaluation and write JSON report."""
    os.makedirs(os.path.join(ARTIFACT_DIR, "evals"), exist_ok=True)

    meta = load_model_metadata()
    feature_cols = meta["feature_cols"]
    target_transform = meta.get("target_transform", "none")

    artifact_path = os.path.join(
        ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.joblib"
    )
    if not os.path.exists(artifact_path):
        raise SystemExit(f"Model artifact not found: {artifact_path}")

    model = joblib.load(artifact_path)

    df = load_labeled_rows(feature_cols)
    train_df, test_df = time_split(df, TEST_FRAC)

    X_test = build_feature_matrix(test_df, feature_cols)
    y_test = test_df[LABEL_COL].astype(float)
    preds_raw = model.predict(X_test)

    if target_transform == "log1p":
        preds = pd.Series(preds_raw).apply(math.expm1).clip(lower=0.0).to_numpy()
    else:
        preds = preds_raw

    test_df = test_df.copy()
    test_df["prediction"] = preds.astype(float)

    # --- Model metrics ---
    overall = compute_metrics(y_test, test_df["prediction"].astype(float))

    # --- Baseline: rolling weighted mean ---
    test_df["baseline_prediction"] = test_df["weighted_mean"].astype(float)
    baseline_overall = compute_metrics(
        y_test, test_df["baseline_prediction"].astype(float)
    )

    # --- Lift vs baseline ---
    lift_mae_pct = float(
        (baseline_overall["mae"] - overall["mae"])
        / baseline_overall["mae"]
        * 100.0
    )
    lift_rmse_pct = float(
        (baseline_overall["rmse"] - overall["rmse"])
        / baseline_overall["rmse"]
        * 100.0
    )

    # --- Breakdown metrics ---
    by_pos = position_metrics(test_df, LABEL_COL, "prediction")
    by_bucket = bucket_metrics(test_df, LABEL_COL, "prediction")

    report = {
        "report_version": 2,
        "generated_at": date.today().isoformat(),
        "model_name": MODEL_NAME,
        "market_code": MARKET_CODE,
        "lookback": LOOKBACK,
        "artifact_path": artifact_path,
        "feature_cols": feature_cols,
        "rows_total": int(len(df)),
        "rows_train_time_split": int(len(train_df)),
        "rows_test_time_split": int(len(test_df)),
        "metrics_test_overall": overall,
        "metrics_test_overall_baseline_weighted_mean": baseline_overall,
        "lift_vs_baseline_pct": {
            "mae_improvement_pct": lift_mae_pct,
            "rmse_improvement_pct": lift_rmse_pct,
        },
        "metrics_test_by_position": by_pos,
        "metrics_test_by_label_bucket": by_bucket,
        "notes": [
            "Evaluation uses time-ordered split (no shuffle).",
            "Baseline = rolling weighted mean.",
            "Positive lift means model beats baseline.",
        ],
    }

    out_path = os.path.join(
        ARTIFACT_DIR,
        "evals",
        f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}_eval.json",
    )

    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print("EVAL COMPLETE")
    print(json.dumps(report["metrics_test_overall"], indent=2))
    print("Baseline:", json.dumps(baseline_overall, indent=2))
    print("Lift (%):", json.dumps(report["lift_vs_baseline_pct"], indent=2))
    print("Wrote:", out_path)


if __name__ == "__main__":
    main()









