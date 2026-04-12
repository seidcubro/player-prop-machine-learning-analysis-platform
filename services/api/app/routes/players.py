"""Player-related API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import joblib
import json
import os

from ..db import get_db

router = APIRouter()


def _clean_feature_payload(feature_map: dict, meta: dict) -> dict:
    feature_cols = list(meta.get("feature_cols") or [])
    if not feature_cols:
        return dict(feature_map)

    cleaned = {}
    for col in feature_cols:
        if col in feature_map:
            cleaned[col] = feature_map[col]
    return cleaned


def _meta_summary(meta: dict) -> dict:
    return {
        "market_name": meta.get("market_name"),
        "feature_family": meta.get("feature_family"),
        "base_feature_cols": list(meta.get("base_feature_cols") or []),
        "extra_feature_cols": list(meta.get("extra_feature_cols") or []),
        "upstream_features_used": [
            c for c in list(meta.get("extra_feature_cols") or [])
            if c.endswith("_mean") or c.endswith("_trend")
        ],
        "model_metrics": {
            "mae": meta.get("mae"),
            "rmse": meta.get("rmse"),
            "r2": meta.get("r2"),
        },
    }


FEATURE_COLS = ["mean", "stddev", "weighted_mean",
                "trend", "aux_mean", "aux_trend"]


def _get_player_row(db: Session, player_id: int):
    row = db.execute(
        text(
            """
            SELECT
              id,
              external_id,
              first_name,
              last_name,
              COALESCE(NULLIF(TRIM(first_name || ' ' || last_name), ''), name, external_id) AS display_name,
              name,
              position,
              team
            FROM players
            WHERE id = :player_id
            """
        ),
        {"player_id": player_id},
    ).mappings().first()
    return row


@router.get("/players")
def list_players(
    search: str | None = Query(default=None),
    include_total: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    where_sql = ""
    params = {"limit": limit, "offset": offset}

    if search and search.strip():
        where_sql = """
            WHERE
              COALESCE(first_name || ' ' || last_name, '') ILIKE :q
              OR COALESCE(first_name, '') ILIKE :q
              OR COALESCE(last_name, '') ILIKE :q
              OR COALESCE(name, '') ILIKE :q
              OR COALESCE(team, '') ILIKE :q
              OR COALESCE(position, '') ILIKE :q
              OR COALESCE(external_id, '') ILIKE :q
        """
        params["q"] = f"%{search.strip()}%"

    rows = db.execute(
        text(
            f"""
            SELECT
              id,
              external_id,
              first_name,
              last_name,
              COALESCE(NULLIF(TRIM(first_name || ' ' || last_name), ''), name, external_id) AS name,
              position,
              team
            FROM players
            {where_sql}
            ORDER BY last_name NULLS LAST, first_name NULLS LAST, name NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()

    resp = {"ok": True, "players": list(rows)}

    if include_total:
        total_row = db.execute(
            text(f"SELECT COUNT(*) AS total FROM players {where_sql}"),
            ({"q": params["q"]} if params.get("q") else {}),
        ).mappings().first()
        resp["total"] = int(total_row["total"]) if total_row else 0

    return resp


@router.get("/players/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    row = _get_player_row(db, player_id)
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")

    return {
        "ok": True,
        "player": {
            "id": row["id"],
            "external_id": row["external_id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "name": row["display_name"],
            "position": row["position"],
            "team": row["team"],
        },
    }


@router.get("/players/{player_id}/projection_ml")
def projection_ml(
    player_id: int,
    market_code: str = Query(...),
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    external_id = player["external_id"]
    if not external_id:
        raise HTTPException(
            status_code=400, detail="Player missing external_id")

    m = db.execute(
        text("SELECT id, code FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()
    if not m:
        raise HTTPException(
            status_code=404, detail=f"Unknown market_code: {market_code}")
    market_id = int(m["id"])

    am = db.execute(
        text(
            """
            SELECT model_name, lookback, artifact_path
            FROM active_models
            WHERE market_id = :market_id
            """
        ),
        {"market_id": market_id},
    ).mappings().first()
    if not am:
        raise HTTPException(
            status_code=404, detail=f"No active model for market_code: {market_code}")

    model_name = am["model_name"]
    artifact_path = am["artifact_path"]

    if int(am["lookback"]) != int(lookback):
        raise HTTPException(
            status_code=400,
            detail=f"Active model lookback is {am['lookback']} but request lookback is {lookback}",
        )

    if not artifact_path or not os.path.exists(artifact_path):
        raise HTTPException(
            status_code=500, detail=f"Model artifact not found at: {artifact_path}")

    meta_path = os.path.splitext(artifact_path)[0] + ".json"
    if not os.path.exists(meta_path):
        raise HTTPException(
            status_code=500, detail=f"Model metadata not found at: {meta_path}")

    with open(meta_path, "r", encoding="utf-8") as f:
        model_meta = json.load(f)

    feature_order = model_meta.get("feature_cols") or model_meta.get("feature_columns")
    if not feature_order:
        raise HTTPException(
            status_code=500, detail=f"Model metadata missing feature_cols in: {meta_path}")

    feat = db.execute(
        text(
            """
            SELECT
              as_of_game_date,
              opponent,
              mean,
              stddev,
              weighted_mean,
              trend,
              COALESCE(aux_mean, COALESCE(recs_mean, 0.0)) AS aux_mean,
              COALESCE(aux_trend, COALESCE(recs_trend, 0.0)) AS aux_trend,
              extra_features
            FROM player_market_features
            WHERE player_id = :external_id
              AND market_id = :market_id
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC
            LIMIT 1
            """
        ),
        {"external_id": external_id, "market_id": market_id, "lookback": lookback},
    ).mappings().first()

    if not feat:
        raise HTTPException(
            status_code=404,
            detail=f"No features found for player external_id={external_id}, market={market_code}, lookback={lookback}",
        )

    features_obj = {
        "mean": float(feat["mean"] or 0.0),
        "stddev": float(feat["stddev"] or 0.0),
        "weighted_mean": float(feat["weighted_mean"] or 0.0),
        "trend": float(feat["trend"] or 0.0),
        "aux_mean": float(feat["aux_mean"] or 0.0),
        "aux_trend": float(feat["aux_trend"] or 0.0),
    }

    extra = feat["extra_features"] or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    elif not isinstance(extra, dict):
        extra = {}

    for k, v in extra.items():
        try:
            features_obj[str(k)] = float(v or 0.0)
        except Exception:
            features_obj[str(k)] = 0.0

    if "targets_mean" in features_obj and "team_pass_attempts" in features_obj:
        denom = float(features_obj.get("team_pass_attempts", 0.0) or 0.0)
        numer = float(features_obj.get("targets_mean", 0.0) or 0.0)
        features_obj["target_share"] = 0.0 if denom == 0.0 else max(0.0, min(1.0, numer / denom))

    X = [[float(features_obj.get(col, 0.0)) for col in feature_order]]

    pipe = joblib.load(artifact_path)
    pred = float(pipe.predict(X)[0])

    db.execute(
        text(
            """
            INSERT INTO ml_projections
              (player_id, market_code, model_name, lookback, as_of_game_date, opponent, prediction, features, artifact_path)
            VALUES
              (:player_id, :market_code, :model_name, :lookback, :as_of_game_date, :opponent, :prediction, CAST(:features AS jsonb), :artifact_path)
            ON CONFLICT (player_id, market_code, model_name, lookback, as_of_game_date)
            DO UPDATE SET
              prediction = EXCLUDED.prediction,
              features = EXCLUDED.features,
              artifact_path = EXCLUDED.artifact_path,
              created_at = NOW()
            """
        ),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": model_name,
            "lookback": lookback,
            "as_of_game_date": feat["as_of_game_date"],
            "opponent": feat["opponent"],
            "prediction": pred,
            "features": json.dumps(features_obj),
            "artifact_path": artifact_path,
        },
    )
    db.commit()

    cleaned_features = _clean_feature_payload(features_obj, model_meta)
    meta_info = _meta_summary(model_meta)

    return {
        "ok": True,
        "player_id": player_id,
        "external_id": external_id,
        "player_name": player["display_name"],
        "market_code": market_code,
        "market_name": meta_info["market_name"],
        "feature_family": meta_info["feature_family"],
        "model_name": model_name,
        "lookback": lookback,
        "as_of_game_date": str(feat["as_of_game_date"]),
        "opponent": feat["opponent"],
        "prediction": pred,
        "features": cleaned_features,
        "base_feature_cols": meta_info["base_feature_cols"],
        "extra_feature_cols": meta_info["extra_feature_cols"],
        "upstream_features_used": meta_info["upstream_features_used"],
        "model_metrics": meta_info["model_metrics"],
        "artifact_path": artifact_path,
    }


@router.get("/players/{player_id}/projection_history")
def projection_history(
    player_id: int,
    market_code: str = Query(...),
    model_name: str = Query("ridge_v1"),
    lookback: int = Query(5, ge=1, le=50),
    limit: int = Query(10, ge=1, le=200),
    db: Session = Depends(get_db),
):
    exists = db.execute(
        text("SELECT 1 FROM players WHERE id = :player_id"),
        {"player_id": player_id},
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Player not found")

    rows = db.execute(
        text(
            """
            SELECT
              player_id,
              market_code,
              model_name,
              lookback,
              as_of_game_date,
              opponent,
              prediction,
              features,
              artifact_path,
              created_at
            FROM ml_projections
            WHERE player_id = :player_id
              AND market_code = :market_code
              AND model_name = :model_name
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC, created_at DESC
            LIMIT :limit
            """
        ),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": model_name,
            "lookback": lookback,
            "limit": limit,
        },
    ).mappings().all()

    artifact_path = None
    for r in rows:
        artifact_path = r["artifact_path"]
        if artifact_path:
            break

    meta_info = {
        "market_name": None,
        "feature_family": None,
        "base_feature_cols": [],
        "extra_feature_cols": [],
        "upstream_features_used": [],
        "model_metrics": {"mae": None, "rmse": None, "r2": None},
    }

    if artifact_path:
        meta_path = os.path.splitext(artifact_path)[0] + ".json"
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                hist_meta = json.load(f)
            meta_info = _meta_summary(hist_meta)

    cleaned_rows = []
    for r in rows:
        row_dict = dict(r)
        row_features = row_dict.get("features") or {}
        if isinstance(row_features, str):
            try:
                row_features = json.loads(row_features)
            except Exception:
                row_features = {}
        elif not isinstance(row_features, dict):
            row_features = {}

        if artifact_path:
            row_dict["features"] = _clean_feature_payload(row_features, hist_meta)
        else:
            row_dict["features"] = row_features

        cleaned_rows.append(row_dict)

    return {
        "ok": True,
        "market_name": meta_info["market_name"],
        "feature_family": meta_info["feature_family"],
        "base_feature_cols": meta_info["base_feature_cols"],
        "extra_feature_cols": meta_info["extra_feature_cols"],
        "upstream_features_used": meta_info["upstream_features_used"],
        "model_metrics": meta_info["model_metrics"],
        "rows": cleaned_rows,
    }


@router.get("/players/{player_id}/projection_baseline")
def get_projection_baseline(
    player_id: int,
    market_code: str = Query(...),
    model_name: str = Query("baseline_v1"),
    db: Session = Depends(get_db),
):
    exists = db.execute(
        text("SELECT 1 FROM players WHERE id = :player_id"),
        {"player_id": player_id},
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Player not found")

    m = db.execute(
        text("SELECT id FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()
    if not m:
        raise HTTPException(
            status_code=404, detail=f"Unknown market_code: {market_code}")

    row = db.execute(
        text(
            """
            SELECT game_date, opponent, model_name, mean, stddev, p_over, created_at
            FROM projections
            WHERE player_id = :player_id
              AND market_id = :market_id
              AND model_name = :model_name
            ORDER BY game_date DESC
            LIMIT 1
            """
        ),
        {"player_id": player_id, "market_id": int(
            m["id"]), "model_name": model_name},
    ).mappings().first()

    if not row:
        raise HTTPException(
            status_code=404, detail="No baseline projection found")

    return {
        "ok": True,
        "player_id": player_id,
        "market_code": market_code,
        "model_name": row["model_name"],
        "game_date": str(row["game_date"]),
        "opponent": row["opponent"],
        "mean": float(row["mean"]),
        "stddev": float(row["stddev"]),
        "p_over": float(row["p_over"]),
        "created_at": str(row["created_at"]),
    }

