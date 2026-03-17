"""Player-related API routes.

These endpoints provide player lookup and projection APIs for the platform.
They serve both baseline projections and ML projections backed by trained
artifacts and stored prediction history.

Notes:
- Baseline projections are stored in the `projections` table.
- ML projections are produced by the inference service and stored in
  `ml_projections` (with history).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()


@router.get("/players")
def list_players(
    q: str | None = Query(default=None, description="Optional name substring filter"),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """List players from the `players` table."""
    if q:
        rows = db.execute(
            text(
                """
                SELECT id, name, position, team
                FROM players
                WHERE LOWER(name) LIKE LOWER(:q)
                ORDER BY name
                LIMIT :limit
                """
            ),
            {"q": f"%{q}%", "limit": limit},
        ).mappings().all()
    else:
        rows = db.execute(
            text(
                """
                SELECT id, name, position, team
                FROM players
                ORDER BY name
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()

    return {"ok": True, "rows": [dict(r) for r in rows]}


@router.get("/players/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    """Get a single player by id."""
    row = db.execute(
        text(
            """
            SELECT id, name, position, team
            FROM players
            WHERE id = :player_id
            """
        ),
        {"player_id": player_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Player not found")

    return {"ok": True, "player": dict(row)}


@router.get("/players/{player_id}/projection_ml")
def get_projection_ml(
    player_id: int,
    market_code: str = Query(..., description="Market code (e.g., rec_yds)"),
    lookback: int = Query(default=5, ge=1, le=20),
    model_name: str | None = Query(default=None, description="Optional override of model_name"),
    db: Session = Depends(get_db),
):
    """Get the latest ML projection for a player/market."""
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

    # Default to active model if not explicitly provided.
    # active_models is keyed by market_id (NOT market_code).
    active = db.execute(
        text(
            """
            SELECT market_id, lookback, model_name, artifact_path
            FROM active_models
            WHERE market_id = :market_id
            LIMIT 1
            """
        ),
        {"market_id": market_id},
    ).mappings().first()

    if not active and not model_name:
        raise HTTPException(status_code=404, detail="No active model configured for this market")

    resolved_model = model_name or active["model_name"]
    resolved_lookback = lookback if lookback is not None else active["lookback"]

    # IMPORTANT: ml_projections uses market_code (no market_id column)
    row = db.execute(
        text(
            """
            SELECT player_id, market_code, model_name, lookback,
                   as_of_game_date, opponent, prediction, features, artifact_path, created_at
            FROM ml_projections
            WHERE player_id = :player_id
              AND market_code = :market_code
              AND model_name = :model_name
              AND lookback = :lookback
            ORDER BY as_of_game_date DESC, created_at DESC
            LIMIT 1
            """
        ),
        {
            "player_id": player_id,
            "market_code": market_code,
            "model_name": resolved_model,
            "lookback": resolved_lookback,
        },
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="No ML projection found")

    return {
        "ok": True,
        "player_id": int(row["player_id"]),
        "market_code": row["market_code"],
        "model_name": row["model_name"],
        "lookback": int(row["lookback"]),
        "as_of_game_date": str(row["as_of_game_date"]),
        "opponent": row["opponent"],
        "prediction": float(row["prediction"]),
        "features": row["features"],
        "artifact_path": row["artifact_path"],
    }


@router.get("/players/{player_id}/projection_history")
def get_projection_history(
    player_id: int,
    market_code: str = Query(..., description="Market code (e.g., rec_yds)"),
    lookback: int = Query(default=5, ge=1, le=20),
    model_name: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Return recent ML projection history rows for a player/market/model/lookback."""
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

    active = db.execute(
        text(
            """
            SELECT model_name, lookback
            FROM active_models
            WHERE market_id = :market_id
            LIMIT 1
            """
        ),
        {"market_id": market_id},
    ).mappings().first()

    if not active and not model_name:
        raise HTTPException(status_code=404, detail="No active model configured for this market")

    resolved_model = model_name or active["model_name"]
    resolved_lookback = lookback if lookback is not None else active["lookback"]

    # IMPORTANT: ml_projections uses market_code (no market_id column)
    rows = db.execute(
        text(
            """
            SELECT player_id, market_code, model_name, lookback,
                   as_of_game_date, opponent, prediction, features, artifact_path, created_at
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
            "model_name": resolved_model,
            "lookback": resolved_lookback,
            "limit": limit,
        },
    ).mappings().all()

    out = []
    for r in rows:
        out.append(
            {
                "player_id": int(r["player_id"]),
                "market_code": r["market_code"],
                "model_name": r["model_name"],
                "lookback": int(r["lookback"]),
                "as_of_game_date": str(r["as_of_game_date"]),
                "opponent": r["opponent"],
                "prediction": float(r["prediction"]),
                "features": r["features"],
                "artifact_path": r["artifact_path"],
                "created_at": str(r["created_at"]),
            }
        )

    return {"ok": True, "rows": out}


@router.get("/players/{player_id}/projection_baseline")
def get_projection_baseline(
    player_id: int,
    market_code: str = Query(..., description="Market code (e.g., rec_yds)"),
    model_name: str = Query(default="baseline_v1"),
    db: Session = Depends(get_db),
):
    """Get the latest stored baseline projection for player/market/model."""
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





