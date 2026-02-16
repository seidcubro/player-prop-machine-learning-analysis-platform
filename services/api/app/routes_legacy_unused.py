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
    search: str | None = Query(
        None,
        description="Case-insensitive search across name, team, position, external_id.",
    ),
    include_total: bool = Query(
        False,
        description="If true, include `total` matching rows for pagination.",
    ),
):
    """List players with server-side pagination and optional search.

    This endpoint is used by the React frontend and any internal tooling that
    needs league-scale player lookup.

    Args:
        db: SQLAlchemy session (injected).
        limit: Maximum number of rows returned (page size).
        offset: Pagination offset (0+).
        search: Optional free-text search applied across:
            - full name (first + last)
            - first_name / last_name
            - team / position
            - external_id
        include_total: If true, also return `total` rows matching the filter to
            support UI pagination.

    Returns:
        dict: `{ "ok": true, "players": [...], "total": <int optional> }`
    """
    params = {"limit": limit, "offset": offset}
    where_sql = ""

    q = (search or "").strip()
    if q:
        params["q"] = f"%{q}%"
        where_sql = """
        WHERE
          (p.first_name || ' ' || p.last_name) ILIKE :q
          OR p.first_name ILIKE :q
          OR p.last_name ILIKE :q
          OR COALESCE(p.team, '') ILIKE :q
          OR COALESCE(p.position, '') ILIKE :q
          OR COALESCE(p.external_id, '') ILIKE :q
        """

    rows = db.execute(
        text(f"""
            SELECT
              p.id,
              p.external_id,
              p.first_name,
              p.last_name,
              (p.first_name || ' ' || p.last_name) AS name,
              p.position,
              p.team
            FROM players p
            {where_sql}
            ORDER BY p.last_name NULLS LAST, p.first_name NULLS LAST, p.id
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()

    payload = {"ok": True, "players": list(rows)}

    if include_total:
        total = db.execute(
            text(f"SELECT COUNT(*) AS total FROM players p {where_sql}"),
            params,
        ).scalar_one()
        payload["total"] = int(total)

    return payload@router.get("/players/{player_id}")
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




