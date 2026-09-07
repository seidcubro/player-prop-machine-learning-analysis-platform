"""Training script for market-specific projection models.

Market-driven training:
- reads market metadata from prop_markets, including eligible_positions
- loads base rolling features from player_market_features, joined to players
  and filtered to eligible_positions (see db/migrations/populate_eligible_positions.sql
  -- without this, training is diluted by rows from positions that structurally
  never produce the stat, e.g. a lineman's rushing yards)
- flattens extra_features JSON into model columns
- derives model-level hybrid features like target_share
- uses a time-ordered train/test split (never shuffled -- this is forecasting)
- trains a RandomForest (default) or GradientBoosting (MODEL_NAME startswith "gb")
- optionally trains on log1p(y) via TARGET_TRANSFORM=log1p -- currently unused in
  production; an eval.py comparison showed it introduces a systematic
  underprediction bias for right-skewed stats like receiving yards (see
  docs/ML_PIPELINE.md "History"). Kept as a supported option, not a default.
- writes model + metadata artifacts
- updates trained_models and active_models

Always validate a new model with eval.py, not just this script's own printed
metrics -- eval.py rebuilds the feature matrix independently and checks bias,
which is what caught the log1p regression above.
"""

import os
import json
import math
from typing import Any

import joblib
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from sklearn.ensemble import (
    RandomForestRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    ExtraTreesRegressor,
)
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

def _env_max_depth(default: str):
    """MAX_DEPTH accepts an int or "none" for unlimited-depth trees."""
    raw = os.getenv("MAX_DEPTH", default).strip()
    return None if raw.lower() in ("none", "") else int(raw)


def _env_max_features(default: str = "sqrt"):
    """MAX_FEATURES accepts "sqrt"/"log2"/"none" or a fraction like 0.5.

    sklearn rejects the string "0.5", so a numeric value has to be converted
    before it reaches the estimator or every fractional config fails.
    """
    raw = os.getenv("MAX_FEATURES", default).strip()
    if raw.lower() in ("sqrt", "log2"):
        return raw.lower()
    if raw.lower() in ("none", ""):
        return None
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return default


def build_model(model_name: str):
    """Build the estimator for a model name.

    The prefix selects the family, chosen per market by `bakeoff.py` rather than
    assumed. Three markets are served by *linear* models: on expanding-window
    folds, ridge beats every tree for rush_att (R2 0.747) and elasticnet wins
    rush_yds and recs. Rushing volume is close to linear in carry share and
    opponent form, and the extra capacity of a forest only adds variance there.
    LightGBM was evaluated and rejected everywhere -- it scored worse with
    train-test gaps of 0.34-0.51, i.e. it was memorising.
    """
    if model_name.startswith("ridge"):
        # Scaled, because a penalised linear model with unscaled features
        # regularises whichever columns happen to have large units.
        return make_pipeline(
            StandardScaler(),
            Ridge(alpha=float(os.getenv("ALPHA", "10.0")), random_state=42),
        )

    if model_name.startswith("enet") or model_name.startswith("elasticnet"):
        return make_pipeline(
            StandardScaler(),
            ElasticNet(
                alpha=float(os.getenv("ALPHA", "0.05")),
                l1_ratio=float(os.getenv("L1_RATIO", "0.3")),
                random_state=42,
                max_iter=5000,
            ),
        )

    if model_name.startswith("xtrees") or model_name.startswith("extra"):
        return ExtraTreesRegressor(
            n_estimators=int(os.getenv("N_ESTIMATORS", "400")),
            max_depth=_env_max_depth("12"),
            min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "5")),
            max_features=_env_max_features("sqrt"),
            random_state=42,
            n_jobs=-1,
        )

    if model_name.startswith("pois"):
        # Touchdowns are rare, non-negative counts: squared-error regression
        # assumes constant variance and a symmetric error cost, neither of which
        # holds for a stat that is 0 in most rows and almost never above 2.
        # Poisson deviance matches that shape, and the fitted mean doubles as a
        # rate -- P(scores at least one) = 1 - exp(-mu) -- which is the quantity
        # an anytime-TD prop is actually priced on.
        #
        # Depth 3 and l2 1.0, not the depth 6 with no penalty this used to run.
        # Every quarterback market is QB-only, which leaves 1,557 training rows
        # against 77 features -- about 20 rows per feature -- and the deeper
        # model was memorising them. On the held-out season it predicted 11.2%
        # under the actual touchdown rate while this one lands at 1.5%, with
        # Poisson deviance improving from 1.131 to 1.072. MAE is identical
        # between them, which is why this went unnoticed: MAE cannot see a
        # symmetric-looking error that is really a systematic shortfall, and it
        # is the wrong scoring rule for a count anyway.
        return HistGradientBoostingRegressor(
            loss="poisson",
            max_iter=int(os.getenv("N_ESTIMATORS", "150")),
            learning_rate=float(os.getenv("LEARNING_RATE", "0.05")),
            max_depth=_env_max_depth("3"),
            min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "30")),
            l2_regularization=float(os.getenv("L2_REG", "1.0")),
            random_state=42,
        )

    if model_name.startswith("hgb"):
        # Same capacity argument as the Poisson variant above, for the
        # quarterback markets that are not counts. Swept against the holdout
        # season, this configuration had the lowest MAE in all three of
        # pass_yds, pass_att and pass_completions, and the lowest deviance in
        # pass_td. No single market clears its own standard error, but one
        # configuration winning all four out of six candidates lands at roughly
        # p = 0.005 under a no-difference null, which is a far stronger signal
        # than any of the four on its own.
        return HistGradientBoostingRegressor(
            loss="squared_error",
            max_iter=int(os.getenv("N_ESTIMATORS", "150")),
            learning_rate=float(os.getenv("LEARNING_RATE", "0.05")),
            max_depth=_env_max_depth("3"),
            min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "30")),
            l2_regularization=float(os.getenv("L2_REG", "1.0")),
            random_state=42,
        )

    if model_name.startswith("gb"):
        return GradientBoostingRegressor(
            n_estimators=int(os.getenv("N_ESTIMATORS", "300")),
            learning_rate=float(os.getenv("LEARNING_RATE", "0.05")),
            max_depth=_env_max_depth("3"),
            min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "1")),
            subsample=float(os.getenv("SUBSAMPLE", "1.0")),
            random_state=42,
        )

    return RandomForestRegressor(
        n_estimators=int(os.getenv("N_ESTIMATORS", "300")),
        max_depth=_env_max_depth("8"),
        min_samples_split=int(os.getenv("MIN_SAMPLES_SPLIT", "10")),
        min_samples_leaf=int(os.getenv("MIN_SAMPLES_LEAF", "5")),
        max_features=_env_max_features("sqrt"),
        random_state=42,
        n_jobs=-1,
    )

DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "app")
DB_USER = os.getenv("POSTGRES_USER", "app")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "app")

MARKET_CODE = os.getenv("MARKET_CODE", "rec_yds")
LOOKBACK = int(os.getenv("LOOKBACK", "5"))
model_name = os.getenv("MODEL_NAME", "rf_default")
ACTIVATE_MODEL = os.getenv("ACTIVATE_MODEL", "1").strip().lower() not in ("0", "false", "no")
MODEL_NAME = model_name
ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")

LABEL_COL = "label_actual"
BASE_FEATURE_COLS = [
    "mean",
    "stddev",
    "weighted_mean",
    "trend",
    "aux_mean",
    "aux_trend",
]


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
    # Base columns
    for c in BASE_FEATURE_COLS:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Extra JSON features
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

    # Derived hybrid features that belong in the model layer, not the DB layer
    # target_share = rolling player targets / team pass volume proxy
    if "targets_mean" in X.columns and "team_pass_attempts" in X.columns:
        denom = pd.to_numeric(X["team_pass_attempts"], errors="coerce").fillna(0.0)
        numer = pd.to_numeric(X["targets_mean"], errors="coerce").fillna(0.0)
        target_share = numer / denom.replace(0, pd.NA)
        X["target_share"] = (
            pd.to_numeric(target_share, errors="coerce")
            .fillna(0.0)
            .clip(lower=0.0, upper=1.0)
        )

    # Remove duplicate columns safely
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
              predict_enabled,
              eligible_positions
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
            raise SystemExit(
                f"Only player-scoped markets supported right now. Got: {m['scope']}"
            )
        if m["target_kind"] != "regression":
            raise SystemExit(
                f"Only regression markets supported right now. Got: {m['target_kind']}"
            )

        market_id = int(m["id"])
        market_name = m["name"]

        cur.execute(
            """
            SELECT
              pmf.player_id,
              p.position,
              pmf.as_of_game_date,
              pmf.opponent,
              pmf.team,
              pmf.mean,
              pmf.stddev,
              pmf.weighted_mean,
              pmf.trend,
              pmf.aux_mean,
              pmf.aux_trend,
              pmf.extra_features,
              pmf.label_actual
            FROM player_market_features pmf
            JOIN players p ON p.external_id = pmf.player_id
            WHERE pmf.market_id = %s
              AND pmf.lookback = %s
              AND pmf.label_actual IS NOT NULL
            ORDER BY pmf.as_of_game_date, pmf.player_id
            """,
            (market_id, LOOKBACK),
        )
        rows = cur.fetchall()

        eligible_positions = m["eligible_positions"]
        if eligible_positions:
            rows = [r for r in rows if r["position"] in eligible_positions]

        if len(rows) < 10:
            raise SystemExit(
                f"Not enough labeled rows to train (need >= 10). Found: {len(rows)}"
            )

        df = pd.DataFrame(rows)
        df["as_of_game_date"] = pd.to_datetime(df["as_of_game_date"], errors="coerce")
        df = df[df["as_of_game_date"].notna()].copy()

        if len(df) < 10:
            raise SystemExit(
                f"Not enough dated labeled rows to train (need >= 10). Found: {len(df)}"
            )

        train_df, test_df = _time_split(df, test_frac=0.25)

        X_train, feature_cols = _build_feature_dataframe(train_df)
        X_test, _ = _build_feature_dataframe(test_df)
        X_test = X_test.reindex(columns=feature_cols, fill_value=0.0)

        TARGET_TRANSFORM = os.getenv("TARGET_TRANSFORM", "none").lower().strip()

        y_train_raw = train_df[LABEL_COL].apply(_safe_float).astype(float)
        y_test = test_df[LABEL_COL].apply(_safe_float).astype(float)

        if TARGET_TRANSFORM == "log1p":
            y_train = y_train_raw.clip(lower=0.0).apply(math.log1p)
        else:
            y_train = y_train_raw

        model = build_model(model_name)
        model.fit(X_train, y_train)

        preds_raw = model.predict(X_test)

        if TARGET_TRANSFORM == "log1p":
            preds = pd.Series(preds_raw).apply(math.expm1).clip(lower=0.0).to_numpy()
        else:
            preds = preds_raw

        mae = float(mean_absolute_error(y_test, preds))
        rmse = float(math.sqrt(mean_squared_error(y_test, preds)))
        r2 = float(r2_score(y_test, preds))

        # Measure on the holdout, then ship a model refit on everything.
        #
        # The metrics above come from a model that never saw the last 25% of
        # rows, which is the only way to make them honest. But that model was
        # also the one being saved, so the artifact serving the site was trained
        # on three quarters of the data and specifically missing the most recent
        # quarter of it. In a league where per-row production falls about 3% a
        # year, the newest games are the ones worth most, and they were the ones
        # being discarded.
        #
        # Refitting on the full set is standard practice and changes nothing
        # about the reported numbers: `mae`, `rmse` and `r2` still describe the
        # held-out fit, because a metric taken from a model scored on its own
        # training rows would be worthless. `shipped_refit_on_all_rows` records
        # which of the two is in the joblib so this can never be misread later.
        shipped = build_model(model_name)
        X_all, all_cols = _build_feature_dataframe(df)
        X_all = X_all.reindex(columns=feature_cols, fill_value=0.0)
        y_all_raw = df[LABEL_COL].apply(_safe_float).astype(float)
        y_all = (y_all_raw.clip(lower=0.0).apply(math.log1p)
                 if TARGET_TRANSFORM == "log1p" else y_all_raw)
        shipped.fit(X_all, y_all)
        print(f"refit on all {len(X_all)} rows for serving "
              f"(metrics above are from the {len(X_train)}-row held-out fit)")

        artifact_path = os.path.join(
            ARTIFACT_DIR, f"{MODEL_NAME}_{MARKET_CODE}_lb{LOOKBACK}.joblib"
        )
        joblib.dump(shipped, artifact_path)

        feature_importances = {}
        if hasattr(shipped, "feature_importances_"):
            feature_importances = {
                col: float(imp)
                for col, imp in sorted(
                    zip(feature_cols, shipped.feature_importances_),
                    key=lambda x: x[1],
                    reverse=True,
                )
            }

        meta = {
            "model_name": MODEL_NAME,
            "market_code": MARKET_CODE,
            "market_name": market_name,
            "market_id": market_id,
            "feature_family": m["feature_family"],
            "stat_field": m["stat_field"],
            "eligible_positions": eligible_positions,
            "lookback": LOOKBACK,
            "model_type": type(model).__name__,
            "target_transform": TARGET_TRANSFORM,
            "feature_cols": feature_cols,
            "base_feature_cols": BASE_FEATURE_COLS,
            "extra_feature_cols": [c for c in feature_cols if c not in BASE_FEATURE_COLS],
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "shipped_refit_on_all_rows": True,
            "shipped_rows": int(len(X_all)),
            "train_date_min": str(train_df["as_of_game_date"].min().date()),
            "train_date_max": str(train_df["as_of_game_date"].max().date()),
            "test_date_min": str(test_df["as_of_game_date"].min().date()),
            "test_date_max": str(test_df["as_of_game_date"].max().date()),
            "mae": mae,
            "rmse": rmse,
            "r2": r2,
            "artifact_path": artifact_path,
            "feature_importances": feature_importances,
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

            # Any training run used to repoint the market's live model, so a
            # throwaway hyperparameter sweep would silently take over what the
            # dashboard serves. Experiments should set ACTIVATE_MODEL=0.
            if not ACTIVATE_MODEL:
                print(
                    f"ACTIVATE_MODEL=0: left active model for {MARKET_CODE} unchanged "
                    f"(trained {MODEL_NAME}, artifact written)"
                )
                conn.commit()
                return

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
