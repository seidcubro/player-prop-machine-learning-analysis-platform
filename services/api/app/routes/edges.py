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
    # Expected value against the offered price. This is the honest ranking: a
    # 60% pick at -180 is worse than a 52% pick at +110, and sorting by win_prob
    # puts them in the wrong order.
    "expected_value": "expected_value",
    # Default ordering: put the league's biggest names at the top.
    #
    # Sorting purely by expected value opened the board on George Holani and a
    # 21.5 rushing line. That is the highest-EV row and the wrong first
    # impression: a board that leads with players you have to look up reads as
    # noise. `star_score` is last season's fantasy production, the closest proxy
    # here for how heavily a player is bet, with EV breaking ties inside it so
    # the ordering is still useful rather than just a popularity list.
    #
    # This is display ordering only. Choosing any column below replaces it, and
    # nothing in the modelling path reads star_score.
    "featured": "COALESCE(resolved.star_score, 0) DESC, expected_value",
    # Best bets first, then by expected value inside them. This is the only
    # verified-profitable selection, so it is worth being able to sort straight
    # to it.
    "best_bet": "best_bet DESC, expected_value",
    "line": "line",
    "projection": "projection",
    "projection_median": "projection_median",
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
    upcoming_only: bool = Query(
        True,
        description="Only games that have not kicked off yet. The dashboard is "
        "for deciding what to bet, so past slates are hidden by default; pass "
        "false to browse historical edges.",
    ),
    sort: str = Query("featured", description=f"Sort key: {'|'.join(_SORTS)}"),
    order: str = Query("desc", description="asc|desc"),
    tier: str | None = Query(
        None, description="Exact tier, unlike min_tier which is 'and up'"),
    best_bets_only: bool = Query(
        False, description="Only the verified-profitable selection"),
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

    if tier is not None and tier not in _TIER_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}")
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

    if tier:
        # Exact, unlike `min_tier` which is "and up". The stat cards use this so
        # that clicking "Strong" shows the 15 strong signals the card claims,
        # rather than the 20 you get from strong-and-above.
        where.append("edge_tier = :tier")
        params["tier"] = tier

    if best_bets_only:
        where.append("best_bet")

    if upcoming_only:
        where.append("commence_time >= NOW()")

    where_sql = " AND ".join(where)
    # The direction suffix only binds to the final expression in the clause, so
    # a multi-key sort has to carry its own direction on every key but the last.
    # Getting this wrong sorted star_score ascending and put the least-known
    # player on the board at the top, which was the exact problem "featured" was
    # added to fix.
    order_sql = f"{_SORTS[sort]} {'ASC' if order == 'asc' else 'DESC'}"

    # Count props, not rows.
    #
    # Every book prices the same prop, so the table renders one row per prop
    # with the other books shown as chips. Counting rows made the pager say
    # "5 signals" above three visible rows. Paging is over props too, below.
    total = db.execute(
        text(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT player_name, market_code, commence_time"
            f"   FROM prop_edges WHERE {where_sql}"
            ") p"
        ),
        params,
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            SELECT
              -- Qualified because the lateral join below also exposes an "id".
              prop_edges.id,
              prop_edges.event_id,
              prop_edges.commence_time,
              prop_edges.home_team,
              prop_edges.away_team,
              prop_edges.player_name,
              prop_edges.market_code,
              prop_edges.bookmaker_key,
              prop_edges.bookmaker_title,
              prop_edges.line,
              prop_edges.price_american,
              prop_edges.model_name,
              prop_edges.model_r2,
              prop_edges.projection,
              prop_edges.projection_median,
              prop_edges.raw_edge,
              prop_edges.win_prob,
              prop_edges.expected_value,
              prop_edges.recommended_side,
              prop_edges.edge_tier,
              prop_edges.created_at,
              prop_edges.value_flag,
              prop_edges.best_bet,
              -- kickoff date for display; the UI shows when the game is, not
              -- just when the edge row happened to be generated
              (prop_edges.commence_time AT TIME ZONE 'UTC')::date AS game_date,
              -- Names are not unique: there is a Josh Allen at QB for Buffalo
              -- and a Josh Allen at C for Tampa Bay. A bare LIMIT 1 picked
              -- whichever row came first, so a prop could link to a completely
              -- different player's profile. Resolve to someone whose position
              -- can actually produce this market's stat, preferring the one
              -- with recent game activity.
              resolved.headshot AS headshot,
              resolved.star_score AS star_score,
              resolved.id AS player_id,
              -- The other books pricing this same prop.
              --
              -- Deduplication moved to the database so the counts would match
              -- the rows, which meant the browser could no longer collect the
              -- alternates itself. They come back as an array so the Book
              -- column can still show where else the prop is available and at
              -- what number, which is the whole point of shopping a line.
              COALESCE(alts.books, '[]'::json) AS alts
            FROM (
                -- One row per prop, keeping the strongest.
                --
                -- Deduplicating here rather than in the browser keeps the
                -- count, the pagination and what is on screen describing the
                -- same thing. Doing it client-side meant page two could start
                -- mid-prop and the totals never matched the rows.
                SELECT DISTINCT ON (player_name, market_code, commence_time) *
                FROM prop_edges
                WHERE {where_sql}
                ORDER BY player_name, market_code, commence_time,
                         best_bet DESC, expected_value DESC NULLS LAST, id
            ) AS prop_edges
            LEFT JOIN LATERAL (
                SELECT p.id, p.headshot, p.star_score
                FROM players p
                JOIN prop_markets m ON m.code = prop_edges.market_code
                WHERE lower(replace(replace(p.name, '.', ''), '-', ' ')) =
                      lower(replace(replace(prop_edges.player_name, '.', ''), '-', ' '))
                  AND (p.position IS NULL OR p.position = ANY(m.eligible_positions))
                ORDER BY (
                    SELECT count(*) FROM player_game_stats_app g
                    WHERE g.player_id = p.external_id
                ) DESC, p.id
                LIMIT 1
            ) resolved ON TRUE
            LEFT JOIN LATERAL (
                SELECT json_agg(json_build_object(
                           'id', a.id,
                           'bookmaker_key', a.bookmaker_key,
                           'bookmaker_title', a.bookmaker_title,
                           'line', a.line,
                           'price_american', a.price_american
                       ) ORDER BY a.expected_value DESC NULLS LAST) AS books
                FROM prop_edges a
                WHERE a.player_name = prop_edges.player_name
                  AND a.market_code = prop_edges.market_code
                  AND a.commence_time = prop_edges.commence_time
                  AND a.id <> prop_edges.id
            ) alts ON TRUE
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
    # Count props, not rows.
    #
    # Every book prices the same prop, so `prop_edges` holds one row per book and
    # the dashboard collapses them into a single row with the alternates shown as
    # chips. Counting raw rows here made the cards disagree with the table
    # underneath them: "Elite 5" above three visible rows, because two of those
    # props were priced at two books each.
    # Count the deduplicated rows, exactly as the table renders them.
    #
    # Two passes of this were wrong. Counting raw rows made "Elite 5" sit above
    # three visible rows, because a prop priced at two books is two rows. Then
    # counting DISTINCT props *per tier* double-counted any prop whose books
    # disagreed on tier, which they can: different prices give different
    # expected values and so different tiers. That put "Total Signals" at 43
    # above a 42-row board.
    #
    # Deduplicating first, by the same rule the row query uses, makes the cards
    # and the table describe one thing.
    # `best_bet DESC` before `expected_value` is load-bearing.
    #
    # Two books can price a prop at an identical expected value. AJ Barner sat
    # at 0.0428 on both, the flag was on the FanDuel row, and the tie broke
    # toward DraftKings, so the summary counted 12 best bets above a 13-row
    # board. Preferring the flagged row makes the tie deterministic and keeps
    # the two queries agreeing. `id` last so the result is stable run to run.
    DEDUPED = """
        SELECT DISTINCT ON (player_name, market_code, commence_time) *
        FROM prop_edges
        WHERE commence_time >= NOW()
        ORDER BY player_name, market_code, commence_time,
                 best_bet DESC, expected_value DESC NULLS LAST, id
    """

    by_market = db.execute(
        text(
            f"""
            SELECT market_code, COUNT(*) AS count,
                   ROUND(AVG(ABS(raw_edge))::numeric, 1) AS avg_abs_edge
            FROM ({DEDUPED}) d
            GROUP BY market_code
            ORDER BY market_code
            """
        )
    ).mappings().all()

    by_tier = db.execute(
        text(f"SELECT edge_tier, COUNT(*) AS count FROM ({DEDUPED}) d "
             "GROUP BY edge_tier")
    ).mappings().all()

    # Counted here, not in the browser.
    #
    # The dashboard was deriving this from whatever page of rows it happened to
    # be holding, so the card read 12 above a 13-row filtered board. Every other
    # number on that row comes from the server; this one has to as well.
    best_bets = db.execute(
        text(f"SELECT COUNT(*) FROM ({DEDUPED}) d WHERE d.best_bet")
    ).scalar_one()

    last_updated = db.execute(
        text("SELECT MAX(created_at) FROM prop_edges")
    ).scalar()

    # How much of the slate actually has prices.
    #
    # A signal count with no denominator is a mystery: 57 looks low without
    # knowing whether that is 57 out of a full board or 57 from the five games a
    # sportsbook happened to be synced for. Only games with posted props can
    # produce a signal at all, so the honest headline is the ratio, and a thin
    # ratio points at the odds sync rather than at the model.
    coverage = db.execute(
        text(
            """
            SELECT
              (SELECT COUNT(*) FROM odds_events
                WHERE commence_time >= NOW()
                  AND commence_time < NOW() + INTERVAL '8 days') AS games_upcoming,
              (SELECT COUNT(DISTINCT p.provider_event_id)
                 FROM odds_player_props p
                 JOIN odds_events e ON e.provider_event_id = p.provider_event_id
                WHERE e.commence_time >= NOW()) AS games_priced,
              (SELECT COUNT(DISTINCT market_key) FROM odds_player_props p
                 JOIN odds_events e ON e.provider_event_id = p.provider_event_id
                WHERE e.commence_time >= NOW()) AS markets_priced
            """
        )
    ).mappings().first()

    return {
        "ok": True,
        "by_market": [dict(r) for r in by_market],
        "by_tier": {r["edge_tier"]: int(r["count"]) for r in by_tier},
        "best_bets": int(best_bets),
        "coverage": dict(coverage) if coverage else None,
        "last_updated": str(last_updated) if last_updated else None,
    }
