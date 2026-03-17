"""Player-related API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import joblib
import json
import os

from ..db import get_db

router = APIRouter()

FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend", "recs_mean", "recs_trend"]


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
              (first_name || ' ' || last_name) ILIKE :q
              OR first_name ILIKE :q
              OR last_name ILIKE :q
              OR COALESCE(team,'') ILIKE :q
              OR COALESCE(position,'') ILIKE :q
              OR COALESCE(external_id,'') ILIKE :q
        """
        params["q"] = f"%{search.strip()}%"

    rows = db.execute(
        text(f"""
            SELECT id, external_id, first_name, last_name,
                   (first_name || ' ' || last_name) AS name,
                   position, team
            FROM players
            {where_sql}
            ORDER BY last_name, first_name
            LIMIT :limit OFFSET :offset
        """),
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
    row = db.execute(
        text("""
            SELECT id, external_id, first_name, last_name,
                   (first_name || ' ' || last_name) AS name,
                   position, team
            FROM players WHERE id = :player_id
        """),
        {"player_id": player_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Player not found")

    return {"ok": True, "player": dict(row)}


@router.get("/players/{player_id}/projection_ml")
def projection_ml(
    player_id: int,
    market_code: str = Query(...),
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    # validate player
    exists = db.execute(
        text("SELECT 1 FROM players WHERE id = :player_id"),
        {"player_id": player_id},
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Player not found")

    # resolve market
    m = db.execute(
        text("SELECT id, code FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()
    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")
    market_id = int(m["id"])

    # active model for market
    am = db.execute(
        text("""
            SELECT model_name, lookback, artifact_path
            FROM active_models
            WHERE market_id = :market_id
        """),
        {"market_id": market_id},
    ).mappings().first()
    if not am:
        raise HTTPException(status_code=404, detail=f"No active model for market_code: {market_code}")

    model_name = am["model_name"]
    artifact_path = am["artifact_path"]

    if int(am["lookback"]) != int(lookback):
        raise HTTPException(
            status_code=400,
            detail=f"Active model lookback is {am['lookback']} but request lookback is {lookback}",
        )

    if not artifact_path or not os.path.exists(artifact_path):
        raise HTTPException(status_code=500, detail=f"Model artifact not found at: {artifact_path}")

    # latest features for this player/market/lookback
    feat = db.execute(
        text("""
            SELECT as_of_game_date, opponent, mean, stddev, weighted_mean, trend,
                   COALESCE(recs_mean, 0.0) AS recs_mean,
                   COALESCE(recs_trend, 0.0) AS recs_trend
            FROM player_market_features
            WHERE player_id = :player_id
              AND market_id = :market_id
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC
            LIMIT 1
        """),
        {"player_id": player_id, "market_id": market_id, "lookback": lookback},
    ).mappings().first()

    if not feat:
        raise HTTPException(status_code=404, detail="No features found for player/market/lookback")

    features_obj = {c: float(feat[c]) for c in FEATURE_COLS}

    # run inference
    X = [[features_obj[c] for c in FEATURE_COLS]]
    pipe = joblib.load(artifact_path)
    pred = float(pipe.predict(X)[0])

    # upsert into ml_projections
    db.execute(
        text("""
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
        """),
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

    return {
        "ok": True,
        "player_id": player_id,
        "market_code": market_code,
        "model_name": model_name,
        "lookback": lookback,
        "as_of_game_date": str(feat["as_of_game_date"]),
        "opponent": feat["opponent"],
        "prediction": pred,
        "features": features_obj,
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
        text("""
            SELECT player_id, market_code, model_name, lookback,
                   as_of_game_date, opponent, prediction, features, artifact_path, created_at
            FROM ml_projections
            WHERE player_id = :player_id
              AND market_code = :market_code
              AND model_name = :model_name
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC, created_at DESC
            LIMIT :limit
        """),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": model_name,
            "lookback": lookback,
            "limit": limit,
        },
    ).mappings().all()

    return {"ok": True, "rows": [dict(r) for r in rows]}


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
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")

    row = db.execute(
        text("""
            SELECT game_date, opponent, model_name, mean, stddev, p_over, created_at
            FROM projections
            WHERE player_id = :player_id
              AND market_id = :market_id
              AND model_name = :model_name
            ORDER BY game_date DESC
            LIMIT 1
        """),
        {"player_id": player_id, "market_id": int(m["id"]), "model_name": model_name},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="No baseline projection found")

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
