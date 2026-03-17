"""Training script for market-specific projection models.

Market-driven training:
- reads market metadata from prop_markets
- loads base rolling features from player_market_features
- flattens extra_features JSON into model columns
- uses a time-ordered train/test split
- writes model + metadata artifacts
- updates trained_models and active_models
"""

import os
import json
import math
from typing import Any

import joblib
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
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

LABEL_COL = "label_actual"
BASE_FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend", "recs_mean", "recs_trend"]


def connect():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )


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

    out: dict[str, float] = {}
    for k, v in raw.items():
        out[str(k)] = _safe_float(v, 0.0)
    return out


def _build_feature_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    # Ensure base columns always exist
    for c in BASE_FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Normalize JSON extra_features
    if "extra_features" not in df.columns:
        df["extra_features"] = None

    extras_series = df["extra_features"].apply(_normalize_extra_features)
    extra_keys: list[str] = sorted({k for d in extras_series for k in d.keys()})

    extra_df = pd.DataFrame(
        [{k: d.get(k, 0.0) for k in extra_keys} for d in extras_series],
        index=df.index,
    )
    if not extra_df.empty:
        extra_df = extra_df.fillna(0.0).astype(float)

    X = df[BASE_FEATURE_COLS].copy()
    if not extra_df.empty:
        X = pd.concat([X, extra_df], axis=1)

    X = X.loc[:, ~X.columns.duplicated()]

    feature_cols = list(X.columns)

    return X.astype(float), feature_cols


def _time_split(
    df: pd.DataFrame,
    test_frac: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "as_of_game_date" not in df.columns:
        raise SystemExit("Missing as_of_game_date; cannot do time-ordered split.")

    df = df.sort_values(["as_of_game_date", "player_id"]).reset_index(drop=True)

    n = len(df)
    if n < 10:
        raise SystemExit(f"Not enough labeled rows to train (need >= 10). Found: {n}")

    split_idx = max(1, int(round(n * (1.0 - test_frac))))
    split_idx = min(split_idx, n - 1)

    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    if len(train_df) < 5 or len(test_df) < 1:
        raise SystemExit(
            f"Bad time split. train_rows={len(train_df)} test_rows={len(test_df)}"
        )

    return train_df, test_df


def main():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)

    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
              id,
              code,
              name,
              stat_field,
              scope,
              target_kind,
              feature_family,
              is_active,
              train_enabled,
              predict_enabled
            FROM prop_markets
            WHERE code = %s
            """,
            (MARKET_CODE,),
        )
        m = cur.fetchone()
        if not m:
            raise SystemExit(f"Market not found: {MARKET_CODE}")

        if not m["is_active"]:
            raise SystemExit(f"Market is inactive: {MARKET_CODE}")
        if not m["train_enabled"]:
            raise SystemExit(f"Training disabled for market: {MARKET_CODE}")
        if m["scope"] != "player":
            raise SystemExit(f"Only player-scoped markets supported right now. Got: {m['scope']}")
        if m["target_kind"] != "regression":
            raise SystemExit(f"Only regression markets supported right now. Got: {m['target_kind']}")

        market_id = int(m["id"])
        market_name = m["name"]

        cur.execute(
            """
            SELECT
              player_id,
              as_of_game_date,
              opponent,
              mean,
              stddev,
              weighted_mean,
              trend,
              recs_mean,
              recs_trend,
              extra_features,
              label_actual
            FROM player_market_features
            WHERE market_id = %s
              AND lookback = %s
              AND label_actual IS NOT NULL
            ORDER BY as_of_game_date, player_id
            """,
            (market_id, LOOKBACK),
        )
        rows = cur.fetchall()
        if len(rows) < 10:
            raise SystemExit(f"Not enough labeled rows to train (need >= 10). Found: {len(rows)}")

        df = pd.DataFrame(rows)
        df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
        df = df[df["as_of_game_date"].notna()].copy()

        if len(df) < 10:
            raise SystemExit(f"Not enough dated labeled rows to train (need >= 10). Found: {len(df)}")

        train_df, test_df = _time_split(df, test_frac=0.25)

        X_train, feature_cols = _build_feature_dataframe(train_df)
        X_test, feature_cols_test = _build_feature_dataframe(test_df)

        # Align test to train columns in case some extras only appear in one side
        X_test = X_test.reindex(columns=feature_cols, fill_value=0.0)

        y_train = train_df[LABEL_COL].apply(_safe_float).astype(float)
        y_test = test_df[LABEL_COL].apply(_safe_float).astype(float)

        pipe = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                ("model", Ridge(alpha=1.0)),
            ]
        )
        pipe.fit(X_train, y_train)

        preds = pipe.predict(X_test)
        mae = float(mean_absolute_error(y_test, preds))
        rmse = float(math.sqrt(mean_squared_error(y_test, preds)))
        r2 = float(r2_score(y_test, preds))

        artifact_path = os.path.join(
            ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.joblib"
        )
        joblib.dump(pipe, artifact_path)

        meta = {
            "model_name": MODEL_NAME,
            "market_code": MARKET_CODE,
            "market_name": market_name,
            "market_id": market_id,
            "feature_family": m["feature_family"],
            "stat_field": m["stat_field"],
            "lookback": LOOKBACK,
            "feature_cols": feature_cols,
            "base_feature_cols": BASE_FEATURE_COLS,
            "extra_feature_cols": [c for c in feature_cols if c not in BASE_FEATURE_COLS],
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "train_date_min": str(train_df["as_of_game_date"].min().date()),
            "train_date_max": str(train_df["as_of_game_date"].max().date()),
            "test_date_min": str(test_df["as_of_game_date"].min().date()),
            "test_date_max": str(test_df["as_of_game_date"].max().date()),
            "mae": mae,
            "rmse": rmse,
            "r2": r2,
            "artifact_path": artifact_path,
        }

        meta_path = os.path.join(
            ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.json"
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        try:
            cur.execute(
                """
                INSERT INTO trained_models
                  (model_name, market_id, lookback, artifact_path, train_rows, test_rows, mae, rmse, r2)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_name, market_id, lookback)
                DO UPDATE SET
                  artifact_path = EXCLUDED.artifact_path,
                  train_rows = EXCLUDED.train_rows,
                  test_rows = EXCLUDED.test_rows,
                  mae = EXCLUDED.mae,
                  rmse = EXCLUDED.rmse,
                  r2 = EXCLUDED.r2,
                  created_at = NOW()
                """,
                (
                    MODEL_NAME,
                    market_id,
                    LOOKBACK,
                    artifact_path,
                    len(X_train),
                    len(X_test),
                    mae,
                    rmse,
                    r2,
                ),
            )

            cur.execute(
                """
                INSERT INTO active_models (market_id, lookback, model_name, artifact_path)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (market_id)
                DO UPDATE SET
                  lookback = EXCLUDED.lookback,
                  model_name = EXCLUDED.model_name,
                  artifact_path = EXCLUDED.artifact_path,
                  updated_at = NOW()
                """,
                (market_id, LOOKBACK, MODEL_NAME, artifact_path),
            )

            conn.commit()
        except Exception as e:
            conn.rollback()
            print("WARN: model registry tables not updated:", e)

        print("TRAINING COMPLETE")
        print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
