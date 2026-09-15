"""Edge-serving API routes.

This is the route that finally connects the edge pipeline to consumers:
services/training/build_prop_edges.py computes line-vs-projection edges and
writes them to prop_edges; before this module existed, nothing read that
table (see docs/API.md "The gap"). The frontend edges dashboard is the
primary consumer.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db

router = APIRouter()

# Whitelisted sort keys -> SQL expressions. Never interpolate raw user input
# into ORDER BY.
_SORTS = {
    # raw_edge is the model's number minus the line, already signed, so this
    # sorts on it directly and descending runs +35 (over) ... 0 ... -35 (under).
    # It used to be stored as a magnitude and re-signed here; once the builder
    # started storing the signed value this negated it a second time and
    # "Edge, descending" put the largest unders at the top.
    "edge": "raw_edge",
    "win_prob": "win_prob",
    # Expected value against the offered price. This is the honest ranking: a
    # 60% pick at -180 is worse than a 52% pick at +110, and sorting by win_prob
    # puts them in the wrong order.
    "expected_value": "expected_value",
    # Profit per unit staked. expected_value above is the model's probability
    # minus the book's implied probability, which is a probability edge, and the
    # two differ by the decimal odds: the same edge pays about twice as much on
    # a plus price as on a heavy minus one. Sorting on the probability edge
    # ranks a -280 and a +172 at the same number.
    "ev_per_unit": "ev_per_unit",
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

def _split_keys(expr: str) -> list[str]:
    """Split a sort expression on its top-level commas.

    A plain `str.split(",")` cuts through the comma inside
    `COALESCE(star_score, 0)` and produces two fragments that are not valid SQL
    on their own, which is how the default ordering briefly returned a 500.
    """
    keys, depth, start = [], 0, 0
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            keys.append(expr[start:i].strip())
            start = i + 1
    keys.append(expr[start:].strip())
    return [k for k in keys if k]


_TIER_ORDER = ["small", "medium", "strong", "elite"]

# Tiers the site actually publishes as picks.
#
# Every tier is still computed, stored and graded, because the tier table on the
# track record page is only meaningful if the losing tiers are in it. What
# changes is what the board offers as a bet. Measured across 7,125 graded picks:
#
#     elite    3,942 picks   +4.0% ROI
#     strong     932 picks   -0.3%
#     medium   1,026 picks   -5.1%
#     small    1,225 picks   -4.3%
#
# Publishing a tier that returns -5% is not a smaller edge, it is a losing bet
# with a label on it. Elite is the only tier that has earned its place; strong
# is kept because it is roughly break-even and gives the board enough rows to be
# useful, and it is the first thing to drop if that stops being true.
PUBLISHED_TIERS = [
    t.strip() for t in
    os.getenv("PUBLISHED_TIERS", "elite,strong").split(",") if t.strip()
]


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
    event_id: str | None = Query(
        None, description="One game, by its provider event id"),
    team: str | None = Query(
        None, description="Either side of a matchup, by team name"),
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

    # Filters split by what they describe.
    #
    # A prop is priced by several books and the board shows one row for it, the
    # strongest. `pre` narrows which props exist at all and is safe to apply
    # before that choice is made, because every row of a prop shares the same
    # player, market and kickoff. `post` describes the row that won, and has to
    # be applied after.
    #
    # Applying a tier filter before the dedup asked a different question: "does
    # this prop have any strong row", which is true for props whose best row is
    # elite. So the same prop was counted under two tiers, and the tier filters
    # summed to 255 against a board total of 216. Clicking "Strong" showed 51
    # signals under a card reading 43.
    pre = ["1=1"]
    post = ["1=1"]
    params: dict = {}

    if market_code:
        pre.append("market_code = :market_code")
        params["market_code"] = market_code
    # Only publishable tiers reach the board, whatever else is asked for.
    post.append("edge_tier = ANY(:published_tiers)")
    params["published_tiers"] = PUBLISHED_TIERS

    if min_tier:
        allowed = _TIER_ORDER[_TIER_ORDER.index(min_tier):]
        post.append("edge_tier = ANY(:tiers)")
        params["tiers"] = allowed
    if side:
        post.append("recommended_side = :side")
        params["side"] = side
    if search:
        pre.append("player_name ILIKE :search")
        params["search"] = f"%{search.strip()}%"

    if tier:
        # Exact, unlike `min_tier` which is "and up". The stat cards use this so
        # that clicking "Strong" shows the strong signals the card claims,
        # rather than everything strong and above.
        post.append("edge_tier = :tier")
        params["tier"] = tier

    if best_bets_only:
        post.append("best_bet")

    if upcoming_only:
        pre.append("commence_time >= NOW()")

    # One game, or one team's players wherever they appear.
    #
    # Both are pre-dedup filters: a prop belongs to a game and to a team no
    # matter which sportsbook priced it, so narrowing before the dedup cannot
    # drop the row that would have survived it.
    if event_id:
        pre.append("event_id = :event_id")
        params["event_id"] = event_id
    if team:
        pre.append("(home_team ILIKE :team OR away_team ILIKE :team)")
        params["team"] = f"%{team.strip()}%"

    pre_sql = " AND ".join(pre)
    post_sql = " AND ".join(post)
    dedup_order = (
        "player_name, market_code, commence_time, "
        "best_bet DESC, expected_value DESC NULLS LAST, id"
    )
    # The direction suffix only binds to the final expression in the clause, so
    # a multi-key sort has to carry its own direction on every key but the last.
    # Getting this wrong sorted star_score ascending and put the least-known
    # player on the board at the top, which was the exact problem "featured" was
    # added to fix.
    # Bind the direction to every key, not just the last one.
    #
    # A composite sort is written "best_bet DESC, expected_value", and appending
    # a direction to that string only reaches `expected_value`. Ordering by
    # best_bet ascending therefore returned best bets first, the opposite of
    # what was asked, and the same shape of mistake once sorted star_score
    # ascending on the default view. Any explicit DESC already written into a
    # key is dropped first so the requested direction wins.
    direction = "ASC" if order == "asc" else "DESC"
    order_sql = ", ".join(
        f"{key.removesuffix(' DESC').removesuffix(' ASC')} {direction}"
        for key in _split_keys(_SORTS[sort])
    )

    # Count props, not rows.
    #
    # Every book prices the same prop, so the table renders one row per prop
    # with the other books shown as chips. Counting rows made the pager say
    # "5 signals" above three visible rows. Paging is over props too, below.
    total = db.execute(
        text(
            "SELECT COUNT(*) FROM ("
            "  SELECT DISTINCT ON (player_name, market_code, commence_time)"
            "         edge_tier, recommended_side, best_bet"
            f"   FROM prop_edges WHERE {pre_sql}"
            f"   ORDER BY {dedup_order}"
            f") d WHERE {post_sql}"
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
              prop_edges.ev_per_unit,
              prop_edges.recommended_side,
              prop_edges.edge_tier,
              prop_edges.created_at,
              prop_edges.value_flag,
              prop_edges.best_bet,
              -- Kickoff date for display, in Eastern rather than UTC.
              --
              -- A Sunday night game kicks off at 20:20 Eastern, which is 00:20
              -- the next day in UTC, so every Sunday, Monday and Thursday night
              -- game was dated a day late on the board: 135 of 842 rows. The
              -- edge builder had the same bug and was fixed; this copy was not.
              (prop_edges.commence_time AT TIME ZONE 'America/New_York')::date AS game_date,
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
                -- One row per prop, keeping the strongest, and only then the
                -- filters that describe that row.
                --
                -- Deduplicating here rather than in the browser keeps the
                -- count, the pagination and what is on screen describing the
                -- same thing. Doing it client-side meant page two could start
                -- mid-prop and the totals never matched the rows.
                SELECT * FROM (
                    SELECT DISTINCT ON (player_name, market_code, commence_time) *
                    FROM prop_edges
                    WHERE {pre_sql}
                    ORDER BY {dedup_order}
                ) d
                WHERE {post_sql}
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
    TIER_PARAMS = {"published_tiers": PUBLISHED_TIERS}

    # Same published-tier restriction the board applies, or the cards count
    # picks the table will not show.
    DEDUPED = """
        SELECT DISTINCT ON (player_name, market_code, commence_time) *
        FROM prop_edges
        WHERE commence_time >= NOW()
          AND edge_tier = ANY(:published_tiers)
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
        ), TIER_PARAMS
    ).mappings().all()

    by_tier = db.execute(
        text(f"SELECT edge_tier, COUNT(*) AS count FROM ({DEDUPED}) d "
             "GROUP BY edge_tier"), TIER_PARAMS
    ).mappings().all()

    # The slate, so the board can be filtered to one game.
    #
    # Counted off the same deduplicated set as every other number here, so the
    # count beside a game matches what selecting it shows. Ordered by kickoff,
    # which is the order somebody building a card for tonight thinks in.
    by_game = db.execute(
        text(
            f"""
            SELECT event_id,
                   MIN(away_team) AS away_team,
                   MIN(home_team) AS home_team,
                   MIN(commence_time) AS commence_time,
                   COUNT(*) AS count,
                   COUNT(*) FILTER (WHERE edge_tier = 'elite') AS elite
            FROM ({DEDUPED}) d
            WHERE event_id IS NOT NULL
            GROUP BY event_id
            ORDER BY MIN(commence_time), MIN(away_team)
            """
        ), TIER_PARAMS
    ).mappings().all()

    # Counted here, not in the browser.
    #
    # The dashboard was deriving this from whatever page of rows it happened to
    # be holding, so the card read 12 above a 13-row filtered board. Every other
    # number on that row comes from the server; this one has to as well.
    best_bets = db.execute(
        text(f"SELECT COUNT(*) FROM ({DEDUPED}) d WHERE d.best_bet"), TIER_PARAMS
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
                WHERE e.commence_time >= NOW()) AS markets_priced,
              -- How many priced players can produce a signal at all.
              --
              -- A sportsbook posts yardage and reception lines on starters and
              -- little else. On a typical slate a large share of the players it
              -- prices carry nothing but an anytime touchdown, which this site
              -- does not publish because the market runs a 37% overround. Those
              -- players are fully projected and have a profile page; they just
              -- have no line to beat.
              --
              -- Without this the board looks like it is missing people. Troy
              -- Franklin was priced at +1200 to score and at nothing else, and
              -- the only honest way to say so is to count it.
              (SELECT COUNT(*) FROM (
                 SELECT p.player_name
                   FROM odds_player_props p
                   JOIN odds_events e ON e.provider_event_id = p.provider_event_id
                  WHERE e.commence_time >= NOW()
                    AND p.player_name NOT ILIKE '%Defense%'
                    AND p.player_name NOT ILIKE '%D/ST%'
                  GROUP BY p.player_name
               ) q) AS players_priced,
              (SELECT COUNT(*) FROM (
                 SELECT p.player_name
                   FROM odds_player_props p
                   JOIN odds_events e ON e.provider_event_id = p.provider_event_id
                  WHERE e.commence_time >= NOW()
                    AND p.market_key <> 'player_anytime_td'
                    AND p.player_name NOT ILIKE '%Defense%'
                    AND p.player_name NOT ILIKE '%D/ST%'
                  GROUP BY p.player_name
               ) q) AS players_with_market,
              -- Priced games that have already kicked off.
              --
              -- The board only serves games that have not started, so the
              -- moment the last game of a slate kicks off the table empties.
              -- Without this the page reports "no prices yet", which is the
              -- opposite of what happened: the prices were bought, the game is
              -- being played, and the next one is days away.
              (SELECT COUNT(DISTINCT p.provider_event_id)
                 FROM odds_player_props p
                 JOIN odds_events e ON e.provider_event_id = p.provider_event_id
                WHERE e.commence_time < NOW()
                  AND e.commence_time > NOW() - INTERVAL '6 hours') AS games_in_progress
            """
        )
    ).mappings().first()

    return {
        "ok": True,
        "by_market": [dict(r) for r in by_market],
        "by_tier": {r["edge_tier"]: int(r["count"]) for r in by_tier},
        "by_game": [dict(r) for r in by_game],
        "best_bets": int(best_bets),
        "coverage": dict(coverage) if coverage else None,
        "last_updated": str(last_updated) if last_updated else None,
    }
