"""Compatibility router module.

Historically, some code imported `router` from `services.api.app.routes`.
This file keeps that import stable by re-exporting the main `APIRouter`.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import get_db

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get("/players")
def list_players(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List players (legacy route module).

    This module predates the split route modules under `services/api/app/routes/`.
    It remains to support older imports and local scripts.

    Args:
        db: SQLAlchemy session (injected).
        limit: Maximum number of rows.
        offset: Pagination offset.

    Returns:
        dict: `{ "ok": true, "players": [...] }`.
    """
    rows = db.execute(
        text("""
            SELECT id, (first_name || ' ' || last_name) AS name, position, team FROM players
            ORDER BY last_name, first_name
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    ).mappings().all()

    return {"ok": True, "players": list(rows)}


@router.get("/players/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    """Fetch a single player record (legacy route module).

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
            SELECT id, (first_name || ' ' || last_name) AS name, position, team FROM players
            WHERE id = :player_id
        """),
        {"player_id": player_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Player not found")

    return {"ok": True, "player": dict(row)}


@router.post("/jobs/attach_labels")
def attach_labels(market_code: str, db: Session = Depends(get_db)):
    """Attach realized outcomes to feature rows (legacy route module).

    Equivalent to `services/api/app/routes/jobs.py::attach_labels`.

    Args:
        market_code: Market code (e.g., "rec_yds").
        db: SQLAlchemy session (injected).

    Returns:
        dict: Summary including updated row count.
    """
    m = db.execute(
        text("SELECT id, code, stat_field FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()

    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")

    allowed_stat_fields = {
        "receiving_yards",
        "receptions",
        "rushing_yards",
        "rush_attempts",
        "passing_yards",
        "passing_tds",
        "touchdowns",
    }

    stat_field = m["stat_field"]
    if stat_field not in allowed_stat_fields:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported stat_field for market {market_code}: {stat_field}",
        )

    sql = f"""
        UPDATE player_market_features pmf
        SET label_actual = pgs.{stat_field}
        FROM player_game_stats_app pgs
        WHERE pmf.player_id = pgs.player_id
          AND pmf.as_of_game_date = pgs.game_date
          AND pmf.opponent = pgs.opponent
          AND pmf.market_id = :market_id
    """

    res = db.execute(text(sql), {"market_id": m["id"]})
    db.commit()

    return {"ok": True, "market_code": market_code, "market_id": m["id"], "updated": res.rowcount}



