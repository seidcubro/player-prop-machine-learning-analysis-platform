"""Player and projection API routes.

Responsibilities:
- player directory endpoints (`/players`, `/players/{id}`)
- baseline projections computed from historical features
- ML projection endpoints that load trained artifacts from the artifacts directory

Data sources:
- Postgres tables populated by ingestion/ETL jobs.
- Model artifacts produced by the training service/job.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import joblib
import json
import os

from ..db import get_db

router = APIRouter()

FEATURE_COLS = ["mean", "stddev", "weighted_mean", "trend"]


@router.get("/players")
def list_players(
    db: Session = Depends(get_db),
    search: str | None = Query(
        default=None,
        description="Case-insensitive search across name, team, position, and external_id.",
    ),
    include_total: bool = Query(
        default=False,
        description="If true, also return `total` rows matching the filter (ignores pagination).",
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List players in alphabetical order with optional search and pagination.

    Filtering behavior:
      - If `search` is provided, the query performs a case-insensitive match (ILIKE)
        against:
          - full name (first_name + last_name)
          - first_name
          - last_name
          - team
          - position
          - external_id

    Pagination behavior:
      - `limit` caps returned rows.
      - `offset` skips rows for paging.

    Args:
        db: SQLAlchemy session (injected).
        search: Optional case-insensitive filter across identifying fields.
        include_total: If true, include the total number of matching rows in the response.
        limit: Maximum number of players to return.
        offset: Row offset for pagination.

    Returns:
        dict: `{ "ok": true, "players": [...], "total": <int> }`
        - `total` is only included when `include_total=true`.
    """
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
            SELECT
              id,
              external_id,
              first_name,
              last_name,
              (first_name || ' ' || last_name) AS name,
              position,
              team
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
    """Fetch a single player record.

    Args:
        player_id: Internal numeric player id (`players.id`).
        db: SQLAlchemy session (injected).

    Returns:
        dict: `{ "ok": true, "player": {...} }`.

    Raises:
        HTTPException: 404 if the player does not exist.
    """
    row = db.execute(
        text("""
            SELECT
              id,
              external_id,
              first_name,
              last_name,
              (first_name || ' ' || last_name) AS name,
              position,
              team
            FROM players
            WHERE id = :player_id
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
    # ensure player exists
    """Generate a model-based projection for the given player and market.

    Workflow:
        1) Validate the player exists.
        2) Resolve `market_code` -> `market_id`.
        3) Fetch the active model for the market from `active_models`.
        4) Validate the requested `lookback` matches the active model.
        5) Load the model pipeline from `artifact_path` (joblib).
        6) Fetch the latest engineered features for the player/market/lookback.
        7) Run inference and upsert the result into `ml_projections`.

    The prediction is tied to the latest available `as_of_game_date` in
    `player_market_features` for this player/market/lookback.

    Args:
        player_id: Internal numeric player id (`players.id`).
        market_code: Market code, e.g. "rec_yds" or "pass_yds".
        lookback: Number of prior games used in the feature window; must match the active model.
        db: SQLAlchemy session (injected).

    Returns:
        dict: Projection payload including `prediction`, `features`, and the resolved `model_name`.

    Raises:
        HTTPException: 404 if player/market/model/features are missing; 400 for lookback mismatch;
            500 if the model artifact file does not exist on disk.
    """
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
            SELECT as_of_game_date, opponent, mean, stddev, weighted_mean, trend
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

    # predict
    X = [[features_obj[c] for c in FEATURE_COLS]]
    pipe = joblib.load(artifact_path)
    pred = float(pipe.predict(X)[0])

    # upsert into ml_projections
    db.execute(
        text("""
            INSERT INTO ml_projections
              (player_id, market_code, model_name, lookback, as_of_game_date, prediction, features)
            VALUES
              (:player_id, :market_code, :model_name, :lookback, :as_of_game_date, :prediction, CAST(:features AS jsonb))
            ON CONFLICT (player_id, market_code, model_name, lookback, as_of_game_date)
            DO UPDATE SET
              prediction = EXCLUDED.prediction,
              features = EXCLUDED.features,
              created_at = NOW()
        """),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": model_name,
            "lookback": lookback,
            "as_of_game_date": feat["as_of_game_date"],
            "prediction": pred,
            "features": json.dumps(features_obj),
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

@router.get("/players/{player_id}/ml_projections")
def ml_projection_history(
    player_id: int,
    market_code: str = Query(...),
    model_name: str = Query("ridge_v1"),
    lookback: int = Query(5, ge=1, le=50),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Retrieve recent saved ML projections for a player.

    This endpoint returns rows previously written by `projection_ml`. It is
    designed for UI history panels and debugging model output over time.

    Args:
        player_id: Internal numeric player id (`players.id`).
        market_code: Market code (e.g., "rec_yds").
        model_name: Model name used when the projection was produced (e.g., "ridge_v1").
        lookback: Lookback window used by the model/features.
        limit: Maximum number of rows to return, ordered by `as_of_game_date` descending.
        db: SQLAlchemy session (injected).

    Returns:
        dict: `{ "ok": true, "rows": [...] }` where each row includes the prediction, features JSON, and timestamp.
    """
    rows = db.execute(
        text("""
            SELECT player_id, market_code, model_name, lookback, as_of_game_date, prediction, features, created_at
            FROM ml_projections
            WHERE player_id = :player_id
              AND market_code = :market_code
              AND model_name = :model_name
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC
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

    return {"ok": True, "rows": list(rows)}

@router.get("/players/{player_id}/projection_baseline")
def projection_baseline(
    player_id: int,
    market_code: str = Query(...),
    model_name: str = Query("baseline_lb5"),
    db: Session = Depends(get_db),
):
    # ensure player exists
    """Fetch the latest precomputed baseline projection for a player/market.

    Baseline projections are stored in the `projections` table (typically produced
    by a separate batch job) and represent a lightweight, non-ML projection
    approach (e.g., normal distribution fit based on historical features).

    This endpoint does not run model inference; it retrieves the most recent
    stored projection row.

    Args:
        player_id: Internal numeric player id (`players.id`).
        market_code: Market code (e.g., "rec_yds").
        model_name: Identifier for the baseline variant as stored in `projections.model_name`.
        db: SQLAlchemy session (injected).

    Returns:
        dict: Projection payload with `mean`, `stddev`, and `p_over` for the latest stored game date.

    Raises:
        HTTPException: 404 if player or market is unknown, or if no baseline projection exists.
    """
    exists = db.execute(
        text("SELECT 1 FROM players WHERE id = :player_id"),
        {"player_id": player_id},
    ).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Player not found")

    m = db.execute(
        text("SELECT id, code FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()
    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")
    market_id = int(m["id"])

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
        {
            "player_id": player_id,
            "market_id": market_id,
            "model_name": model_name,
        },
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


