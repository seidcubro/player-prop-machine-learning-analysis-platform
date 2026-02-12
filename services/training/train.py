"""Training script for market-specific projection models.

This module trains models for a specific prop market (e.g., receiving yards),
writes trained artifacts (model + metadata) to the artifacts directory, and can
optionally evaluate performance.

Artifacts are later consumed by the API and/or inference service.
"""

import os
import json
import joblib
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from sklearn.model_selection import train_test_split
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

FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend"]
LABEL_COL = "label_actual"

def connect():
    """Open a psycopg2 connection to the platform Postgres database.

    Connection parameters are read from environment variables:
        POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

    Returns:
        psycopg2.extensions.connection: Open database connection.

    Raises:
        psycopg2.OperationalError: If the database is unreachable or credentials are invalid.
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )

def main():
    """Train and register a market-specific regression model.

    This script trains a scikit-learn pipeline (StandardScaler + Ridge) on rows
    from `player_market_features` that have `label_actual` populated. It writes:
        - a `.joblib` artifact containing the fitted pipeline
        - a `.json` metadata file with feature columns and evaluation metrics

    Optionally, it updates model registry tables if they exist:
        - `trained_models` (metrics + artifact path)
        - `active_models` (current artifact used by the API per market)

    Environment variables:
        MARKET_CODE: Which market to train (prop_markets.code)
        LOOKBACK: Lookback window (must match feature rows)
        MODEL_NAME: Artifact/model identifier (e.g., "ridge_v1")
        ARTIFACT_DIR: Output directory for model files

    Returns:
        None

    Raises:
        SystemExit: If the market does not exist or there is not enough labeled data to train.
    """
    os.makedirs(ARTIFACT_DIR, exist_ok=True)

    with connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM prop_markets WHERE code = %s", (MARKET_CODE,))
        m = cur.fetchone()
        if not m:
            raise SystemExit(f"Market not found: {MARKET_CODE}")
        market_id = int(m["id"])
        market_name = m["name"]

        cur.execute(
            """
            SELECT mean, stddev, weighted_mean, trend, label_actual
            FROM player_market_features
            WHERE market_id = %s
              AND lookback = %s
              AND label_actual IS NOT NULL
            """,
            (market_id, LOOKBACK),
        )
        rows = cur.fetchall()

        if len(rows) < 10:
            raise SystemExit(f"Not enough labeled rows to train (need >= 10). Found: {len(rows)}")

        df = pd.DataFrame(rows)
        X = df[FEATURE_COLS].astype(float)
        y = df[LABEL_COL].astype(float)

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=42
        )

        pipe = Pipeline(
            steps=[
                ("scaler", StandardScaler()),
                ("model", Ridge(alpha=1.0, random_state=42)),
            ]
        )
        pipe.fit(X_train, y_train)

        preds = pipe.predict(X_test)

        mae = float(mean_absolute_error(y_test, preds))
        rmse = float(mean_squared_error(y_test, preds, squared=False))
        r2 = float(r2_score(y_test, preds))

        artifact_path = os.path.join(ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.joblib")
        joblib.dump(pipe, artifact_path)

        meta = {
            "model_name": MODEL_NAME,
            "market_code": MARKET_CODE,
            "market_name": market_name,
            "lookback": LOOKBACK,
            "feature_cols": FEATURE_COLS,
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "mae": mae,
            "rmse": rmse,
            "r2": r2,
            "artifact_path": artifact_path,
        }
        with open(os.path.join(ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.json"), "w") as f:
            json.dump(meta, f, indent=2)

        # store metrics if tables exist (optional)
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
                (MODEL_NAME, market_id, LOOKBACK, artifact_path, len(X_train), len(X_test), mae, rmse, r2),
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
