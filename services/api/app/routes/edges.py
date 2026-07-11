"""Edge-serving API routes.

This is the route that finally connects the edge pipeline to consumers:
services/training/build_prop_edges.py computes line-vs-projection edges and
writes them to prop_edges; before this module existed, nothing read that
table (see docs/API.md "The gap"). The frontend edges dashboard is the
primary consumer.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()

# Whitelisted sort keys -> SQL expressions. Never interpolate raw user input
# into ORDER BY.
_SORTS = {
    # raw_edge is stored as a positive magnitude for both sides; sign it by
    # recommended side so desc runs +35 (over) ... 0 ... -35 (under).
    "edge": "(CASE WHEN recommended_side = 'under' THEN -raw_edge ELSE raw_edge END)",
    "win_prob": "win_prob",
    "line": "line",
    "projection": "projection",
    "commence_time": "commence_time",
    "player_name": "player_name",
}

_TIER_ORDER = ["small", "medium", "strong", "elite"]


@router.get("/edges")
def list_edges(
    market_code: str | None = Query(None, description="Filter to one market, e.g. rec_yds"),
    min_tier: str | None = Query(None, description="Minimum edge tier: small|medium|strong|elite"),
    side: str | None = Query(None, description="Filter by recommended side: over|under"),
    search: str | None = Query(None, description="Case-insensitive player name search"),
    sort: str = Query("edge", description=f"Sort key: {'|'.join(_SORTS)}"),
    order: str = Query("desc", description="asc|desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List computed betting edges (sportsbook line vs. model projection).

    Rows come from prop_edges, produced by build_prop_edges.py. Each row is one
    (event, market, player, bookmaker, line) with the model's projection, the
    raw edge, win probability, recommended side, and edge tier.
    """
    if sort not in _SORTS:
        raise HTTPException(status_code=400, detail=f"Invalid sort key: {sort}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"Invalid order: {order}")
    if min_tier is not None and min_tier not in _TIER_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid min_tier: {min_tier}")
    if side is not None and side not in ("over", "under"):
        raise HTTPException(status_code=400, detail=f"Invalid side: {side}")

    where = ["1=1"]
    params: dict = {}

    if market_code:
        where.append("market_code = :market_code")
        params["market_code"] = market_code
    if min_tier:
        allowed = _TIER_ORDER[_TIER_ORDER.index(min_tier):]
        where.append("edge_tier = ANY(:tiers)")
        params["tiers"] = allowed
    if side:
        where.append("recommended_side = :side")
        params["side"] = side
    if search:
        where.append("player_name ILIKE :search")
        params["search"] = f"%{search.strip()}%"

    where_sql = " AND ".join(where)
    order_sql = f"{_SORTS[sort]} {'ASC' if order == 'asc' else 'DESC'}"

    total = db.execute(
        text(f"SELECT COUNT(*) FROM prop_edges WHERE {where_sql}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT
              id,
              event_id,
              commence_time,
              home_team,
              away_team,
              player_name,
              market_code,
              bookmaker_key,
              bookmaker_title,
              line,
              price_american,
              model_name,
              model_r2,
              projection,
              raw_edge,
              win_prob,
              recommended_side,
              edge_tier,
              created_at
            FROM prop_edges
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()

    return {
        "ok": True,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "edges": [dict(r) for r in rows],
    }


@router.get("/edges/summary")
def edges_summary(db: Session = Depends(get_db)):
    """Aggregate stats for the dashboard header: counts per market and per tier."""
    by_market = db.execute(
        text(
            """
            SELECT market_code, COUNT(*) AS count,
                   ROUND(AVG(ABS(raw_edge))::numeric, 1) AS avg_abs_edge
            FROM prop_edges
            GROUP BY market_code
            ORDER BY market_code
            """
        )
    ).mappings().all()

    by_tier = db.execute(
        text(
            """
            SELECT edge_tier, COUNT(*) AS count
            FROM prop_edges
            GROUP BY edge_tier
            """
        )
    ).mappings().all()

    last_updated = db.execute(
        text("SELECT MAX(created_at) FROM prop_edges")
    ).scalar()

    return {
        "ok": True,
        "by_market": [dict(r) for r in by_market],
        "by_tier": {r["edge_tier"]: int(r["count"]) for r in by_tier},
        "last_updated": str(last_updated) if last_updated else None,
    }
