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
              team,
              headshot,
              jersey_number,
              height,
              weight,
              college,
              years_exp,
              status,
              rookie_year
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
    positions: str | None = Query(
        default=None,
        description="Comma-separated position whitelist, e.g. QB,RB,WR,TE,FB. "
        "The public site passes offensive positions only; defensive/K/P players "
        "stay in the DB for future markets but are hidden from browsing.",
    ),
    active_only: bool = Query(
        default=False,
        description="Restrict to players with game activity in the last two "
        "seasons. The full table holds 25k historical players going back "
        "decades, which is useless to browse on the public site.",
    ),
    include_total: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    clauses = []
    params = {"limit": limit, "offset": offset}

    if search and search.strip():
        clauses.append(
            """(
              COALESCE(first_name || ' ' || last_name, '') ILIKE :q
              OR COALESCE(first_name, '') ILIKE :q
              OR COALESCE(last_name, '') ILIKE :q
              OR COALESCE(name, '') ILIKE :q
              OR COALESCE(team, '') ILIKE :q
              OR COALESCE(position, '') ILIKE :q
              OR COALESCE(external_id, '') ILIKE :q
            )"""
        )
        params["q"] = f"%{search.strip()}%"

    if positions and positions.strip():
        pos_list = [p.strip().upper() for p in positions.split(",") if p.strip()]
        if pos_list:
            clauses.append("position = ANY(:positions)")
            params["positions"] = pos_list

    if active_only:
        clauses.append(
            """EXISTS (
                 SELECT 1 FROM player_game_stats_app g
                 WHERE g.player_id = players.external_id
                   AND g.season >= (SELECT MAX(season) - 1 FROM player_game_stats_app)
               )"""
        )

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

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
              team,
              headshot,
              jersey_number,
              height,
              weight,
              college,
              years_exp,
              status,
              rookie_year
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
        count_params = {k: v for k, v in params.items() if k in ("q", "positions")}
        total_row = db.execute(
            text(f"SELECT COUNT(*) AS total FROM players {where_sql}"),
            count_params,
        ).mappings().first()
        resp["total"] = int(total_row["total"]) if total_row else 0

    return resp


@router.get("/players/{player_id}/games")
def player_games(
    player_id: int,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Recent game log for a player (most recent first), from player_game_stats_app."""
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    if not player["external_id"]:
        raise HTTPException(status_code=400, detail="Player missing external_id")

    rows = db.execute(
        text(
            """
            SELECT
              game_date,
              season,
              week,
              team,
              opponent,
              COALESCE(targets, 0) AS targets,
              COALESCE(receptions, 0) AS receptions,
              COALESCE(receiving_yards, 0) AS receiving_yards,
              COALESCE(receiving_tds, 0) AS receiving_tds,
              COALESCE(carries, 0) AS carries,
              COALESCE(rushing_yards, 0) AS rushing_yards,
              COALESCE(rushing_tds, 0) AS rushing_tds,
              COALESCE(attempts, 0) AS pass_attempts,
              COALESCE(completions, 0) AS completions,
              COALESCE(passing_yards, 0) AS passing_yards,
              COALESCE(passing_tds, 0) AS passing_tds
            FROM player_game_stats_app
            WHERE player_id = :external_id
              AND game_date IS NOT NULL
            ORDER BY game_date DESC
            LIMIT :limit
            """
        ),
        {"external_id": player["external_id"], "limit": limit},
    ).mappings().all()

    return {
        "ok": True,
        "player_id": player_id,
        "position": player["position"],
        "games": [dict(r) for r in rows],
    }


@router.get("/players/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)):
    row = _get_player_row(db, player_id)
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")

    # Return the whole row: the profile page renders headshot, physicals,
    # college and experience, and hand-listing fields here meant new columns
    # silently never reached the UI.
    player = dict(row)
    player["name"] = row["display_name"]
    player.pop("display_name", None)

    return {"ok": True, "player": player}


# The three projection endpoints that used to live here have been removed:
#
#   /projection_ml         built a projection from the player's stored feature
#                          row with no freshness correction, so it inherited an
#                          arbitrarily old depth chart, injury status, opponent
#                          and Vegas line -- the exact bug fixed in
#                          build_prop_edges.py. It was also a second, divergent
#                          inference path that disagreed with the edges the site
#                          actually serves (see docs/specs/ml-design.md).
#   /projection_history    defaulted to model_name="ridge_v1"
#   /projection_baseline   defaulted to model_name="baseline_v1"
#
# Both defaults name models that no longer exist, so those two returned 404 for
# every request. Nothing in apps/web called any of the three. Projections are
# served from prop_edges via /edges and /players/{id}/edge_history, which is the
# single path with the freshness contract applied.


@router.get("/players/{player_id}/edge_history")
def player_edge_history(
    player_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Graded track record for one player: past picks and whether they won.

    Reads `prop_edge_results`, which joins each historical edge to what the
    player actually did in that game (see `services/training/grade_edges.py`).
    This is what makes a quoted win probability accountable rather than
    decorative -- the summary compares average predicted win probability against
    the rate actually achieved.

    Pushes (actual landed exactly on the line) are stored with `hit = NULL` and
    excluded from the hit rate, matching how a sportsbook settles them.
    """
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="player not found")

    rows = db.execute(
        text(
            """
            SELECT
              r.edge_id,
              r.game_date,
              r.market_code,
              r.line,
              r.projection,
              r.recommended_side,
              r.win_prob,
              r.edge_tier,
              r.actual,
              r.hit,
              r.home_team,
              r.away_team,
              r.bookmaker_title,
              r.price_american
            FROM prop_edge_results r
            WHERE r.player_id = :ext
            ORDER BY r.game_date DESC, r.market_code
            LIMIT :limit
            """
        ),
        {"ext": player["external_id"], "limit": limit},
    ).mappings().all()

    summary = db.execute(
        text(
            """
            SELECT
              COUNT(*) FILTER (WHERE hit IS NOT NULL)          AS graded,
              COUNT(*) FILTER (WHERE hit)                      AS wins,
              COUNT(*) FILTER (WHERE hit IS NULL)              AS pushes,
              AVG(win_prob) FILTER (WHERE hit IS NOT NULL)     AS avg_predicted
            FROM prop_edge_results r
            WHERE r.player_id = :ext
            """
        ),
        {"ext": player["external_id"]},
    ).mappings().first()

    graded = int(summary["graded"] or 0)
    wins = int(summary["wins"] or 0)

    return {
        "ok": True,
        "player_id": player_id,
        "summary": {
            "graded": graded,
            "wins": wins,
            "losses": graded - wins,
            "pushes": int(summary["pushes"] or 0),
            "hit_rate": (wins / graded) if graded else None,
            "avg_predicted": (
                float(summary["avg_predicted"]) if summary["avg_predicted"] else None
            ),
        },
        "history": [dict(r) for r in rows],
    }


@router.get("/projections")
def list_projections(
    market_code: str | None = Query(None, description="Filter to one market"),
    position: str | None = Query(None, description="QB, RB, WR, TE or FB"),
    team: str | None = Query(None),
    search: str | None = Query(None, description="Player name, case insensitive"),
    starters_only: bool = Query(False, description="Depth chart rank 1 only"),
    sort: str = Query("projection"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Projections for every eligible player with an upcoming game.

    This is the core output of the platform. Unlike `/edges`, it does not depend
    on a sportsbook having posted a line: every skill player and quarterback on a
    current depth chart gets a number for every market their position can
    produce, with the predicted distribution attached.
    """
    sorts = {
        "projection": "projection",
        "player_name": "player_name",
        "game_date": "game_date",
        "depth_rank": "depth_rank",
    }
    if sort not in sorts:
        raise HTTPException(status_code=400, detail=f"Invalid sort key: {sort}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"Invalid order: {order}")

    where = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}
    if market_code:
        where.append("market_code = :market_code")
        params["market_code"] = market_code
    if position:
        where.append("position = :position")
        params["position"] = position.upper()
    if team:
        where.append("team = :team")
        params["team"] = team.upper()
    if search and search.strip():
        where.append("player_name ILIKE :search")
        params["search"] = f"%{search.strip()}%"
    if starters_only:
        where.append("depth_rank <= 1")

    where_sql = " AND ".join(where)
    order_sql = f"{sorts[sort]} {'ASC' if order == 'asc' else 'DESC'} NULLS LAST"

    total = db.execute(
        text(f"SELECT COUNT(*) FROM player_projections WHERE {where_sql}"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT pr.player_id, pr.player_name, pr.position, pr.team, pr.opponent,
                   pr.game_date, pr.market_code, pr.projection,
                   pr.p10, pr.p25, pr.p50, pr.p75, pr.p90,
                   pr.model_name, pr.depth_rank, pr.is_starter,
                   p.id AS app_player_id, p.headshot
            FROM player_projections pr
            LEFT JOIN players p ON p.external_id = pr.player_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()

    return {
        "ok": True,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "projections": [dict(r) for r in rows],
    }


@router.get("/players/{player_id}/projections")
def player_projections(player_id: int, db: Session = Depends(get_db)):
    """All upcoming projections for one player, across every market."""
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="player not found")

    rows = db.execute(
        text(
            """
            SELECT market_code, game_date, opponent, projection,
                   p10, p25, p50, p75, p90, model_name, depth_rank
            FROM player_projections
            WHERE player_id = :ext
            ORDER BY game_date, market_code
            """
        ),
        {"ext": player["external_id"]},
    ).mappings().all()

    return {"ok": True, "player_id": player_id, "projections": [dict(r) for r in rows]}
