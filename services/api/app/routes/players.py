"""Player-related API routes."""


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

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
            -- Players who actually play, first.
            --
            -- Alphabetical by surname put Adrian McBride, a receiver with no
            -- games in the database, above Trey McBride, who is a starting
            -- tight end. Nine players share that surname and eight of them are
            -- not the one anybody is searching for. Sorting by most recent game
            -- puts the answer at the top and leaves the retired and the
            -- never-played below it, still findable.
            --
            -- Joined against one grouped pass, not a correlated subquery. As a
            -- subquery this ran once per player, 25,079 index probes for a
            -- fifty row page, and cost 98ms of the endpoint's 145.
            LEFT JOIN (
                SELECT player_id, max(game_date) AS last_game
                FROM player_game_stats_app
                GROUP BY player_id
            ) lg ON lg.player_id = players.external_id
            {where_sql}
            -- Alphabetical, because this is a directory.
            --
            -- It used to lead with lg.last_game DESC, so whichever two teams
            -- played most recently occupied the top of the list in their own
            -- alphabetical run, and the page appeared to start at the Broncos
            -- and Chiefs before restarting at A. Recency is a sensible way to
            -- rank a feed and a confusing way to sort a phone book. The
            -- active-only filter already handles relevance.
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


@router.get("/players/{player_id}/games/{game_date}")
def player_game_detail(
    player_id: int,
    game_date: str,
    db: Session = Depends(get_db),
):
    """One played game: what we projected, what happened, and whether it hit.

    Answers the question the site could not answer about its own output. The
    board only ever showed upcoming numbers, and `player_projections` is wiped
    on every build, so a projection disappeared the moment it could be checked.
    A player with no published pick left no trace at all: Trey McBride is
    projected every week and had zero stored rows, because a pick needs a
    sportsbook line we paid for and a tier that cleared the filters.

    Three sources, joined on the game:
      * `player_projection_history` is what we said before kickoff.
      * `player_game_stats_app` is what actually happened.
      * `prop_edge_results` is the pick, where one was published, with the line
        and whether it won. Absent for most player-games, which is honest: no
        pick was made.
    """
    player = _get_player_row(db, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    stat = db.execute(
        text("""
            SELECT game_date, season, week, team, opponent,
                   COALESCE(targets, 0)          AS targets,
                   COALESCE(receptions, 0)       AS recs,
                   COALESCE(receiving_yards, 0)  AS rec_yds,
                   COALESCE(receiving_tds, 0)    AS rec_td,
                   COALESCE(carries, 0)          AS rush_att,
                   COALESCE(rushing_yards, 0)    AS rush_yds,
                   COALESCE(rushing_tds, 0)      AS rush_td,
                   COALESCE(attempts, 0)         AS pass_att,
                   COALESCE(completions, 0)      AS pass_completions,
                   COALESCE(passing_yards, 0)    AS pass_yds,
                   COALESCE(passing_tds, 0)      AS pass_td,
                   COALESCE(rushing_tds, 0) + COALESCE(receiving_tds, 0) AS any_td
            FROM player_game_stats_app
            WHERE player_id = :ext AND game_date = CAST(:d AS date)
        """),
        {"ext": player["external_id"], "d": game_date},
    ).mappings().first()
    if not stat:
        raise HTTPException(status_code=404, detail="No game on that date")

    projections = db.execute(
        text("""
            SELECT market_code, projection, p10, p25, p50, p75, p90, model_name
            FROM player_projection_history
            WHERE player_id = :ext AND game_date = CAST(:d AS date)
            ORDER BY market_code
        """),
        {"ext": player["external_id"], "d": game_date},
    ).mappings().all()

    picks = db.execute(
        text("""
            SELECT market_code, line, recommended_side, projection,
                   projection_median, win_prob, edge_tier, best_bet,
                   price_american, bookmaker_title, actual, hit,
                   expected_value, ev_per_unit
            FROM prop_edge_results
            WHERE player_id = :ext AND game_date = CAST(:d AS date)
            ORDER BY market_code
        """),
        {"ext": player["external_id"], "d": game_date},
    ).mappings().all()

    # The actual figure for a market, keyed the way the markets are named, so
    # the page can line a projection up against its own outcome without
    # hardcoding the mapping in the browser.
    actuals = {
        "recs": stat["recs"], "rec_yds": stat["rec_yds"],
        "rec_td": stat["rec_td"], "rush_att": stat["rush_att"],
        "rush_yds": stat["rush_yds"], "rush_td": stat["rush_td"],
        "pass_att": stat["pass_att"], "pass_yds": stat["pass_yds"],
        "pass_td": stat["pass_td"], "any_td": stat["any_td"],
        "pass_completions": stat["pass_completions"],
    }
    picks_by_market = {p["market_code"]: dict(p) for p in picks}

    lines = []
    for pr in projections:
        m = pr["market_code"]
        lines.append({
            **dict(pr),
            "actual": actuals.get(m),
            "pick": picks_by_market.get(m),
        })
    # Markets we bet but never stored a projection for still belong on the page.
    for m, pk in picks_by_market.items():
        if not any(l["market_code"] == m for l in lines):
            lines.append({"market_code": m, "projection": pk["projection"],
                          "p50": pk["projection_median"], "actual": actuals.get(m),
                          "pick": pk, "model_name": None,
                          "p10": None, "p25": None, "p75": None, "p90": None})

    return {
        "ok": True,
        "player_id": player_id,
        "game": dict(stat),
        "markets": sorted(lines, key=lambda r: r["market_code"]),
    }


@router.get("/projections")
def list_projections(
    market_code: str | None = Query(None, description="Filter to one market"),
    position: str | None = Query(None, description="QB, RB, WR, TE or FB"),
    team: str | None = Query(None),
    search: str | None = Query(None, description="Player name, case insensitive"),
    starters_only: bool = Query(False, description="Depth chart rank 1 only"),
    game_id: str | None = Query(None, description="One game, by nflverse game id"),
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
        # Sorts on whichever figure the page actually leads with, which is the
        # median everywhere except the three touchdown markets that are never
        # priced. Their median is an integer and almost always zero, so the page
        # shows the expected count instead; sorting on the median there would
        # have put nearly every row in a tie at zero. Kept in step with
        # `displayProjection` in apps/web/src/lib/markets.ts.
        #
        # Passing touchdowns joined the list when the site started showing
        # their mean: a median of 1 or 2 for every starter sorted as a tie.
        "projection": (
            "CASE WHEN market_code IN ('any_td', 'rush_td', 'rec_td', 'pass_td')"
            "     THEN projection ELSE COALESCE(p50, projection) END"
        ),
        "mean": "projection",
        "player_name": "player_name",
        "game_date": "game_date",
        "depth_rank": "depth_rank",
        # Grouped by game: kickoff order, then each matchup together, then the
        # biggest numbers first inside it. The matchup key is the two teams in
        # a fixed order, so both sides of one game land in one block rather
        # than splitting on whose row it is. Always chronological, whatever
        # `order` says, because a slate read backwards is not a useful view.
        "game": (
            "game_date ASC, LEAST(team, opponent), GREATEST(team, opponent), "
            "CASE WHEN market_code IN ('any_td', 'rush_td', 'rec_td', 'pass_td')"
            "     THEN projection ELSE COALESCE(p50, projection) END"
        ),
    }
    if sort not in sorts:
        raise HTTPException(status_code=400, detail=f"Invalid sort key: {sort}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"Invalid order: {order}")

    # Every column qualified with the projections alias.
    #
    # The row query joins `players` to pick up a headshot, and both tables have
    # `position` and `team`. Unqualified, Postgres rejects the filter as
    # ambiguous and the request returns a 500, so the position dropdown on the
    # projections page failed on every value except "all positions". The count
    # query has no join and so never saw it, which is why the failure only
    # showed up on the page and not in the total.
    where = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}
    if market_code:
        where.append("pr.market_code = :market_code")
        params["market_code"] = market_code
    if position:
        where.append("pr.position = :position")
        params["position"] = position.upper()
    if team:
        where.append("pr.team = :team")
        params["team"] = team.upper()
    if search and search.strip():
        where.append("pr.player_name ILIKE :search")
        params["search"] = f"%{search.strip()}%"
    if starters_only:
        where.append("pr.depth_rank <= 1")
    if game_id:
        where.append("pr.game_id = :game_id")
        params["game_id"] = game_id

    where_sql = " AND ".join(where)
    order_sql = f"{sorts[sort]} {'ASC' if order == 'asc' else 'DESC'} NULLS LAST"
    if sort == "game":
        # The direction applies to the size ordering inside each game, never
        # to the games themselves; see the comment on the sort key.
        order_sql = f"{sorts[sort]} DESC NULLS LAST, player_name"

    # One row per player and market: the next game, not every game in the
    # projection window.
    #
    # `build_projections.py` projects eight days ahead, so in a week with a
    # Thursday game every player on those two teams gets two rows for the same
    # market. The page listed both, which read as a duplicate, and the board
    # only ever carries the priced slate, so the freshness audit was comparing a
    # Sunday edge against a Thursday projection and calling the difference a
    # disagreement. Cook's rush_yds was 64.7 against Houston and 71.9 against
    # Detroit; both were right, they were just different games.
    #
    # Every filter above is a property of the player or the market rather than
    # the game, so narrowing before the dedup cannot drop the row that would
    # have survived it.
    dedup_sql = f"""
        SELECT DISTINCT ON (pr.player_id, pr.market_code) pr.*
        FROM player_projections pr
        WHERE {where_sql}
        ORDER BY pr.player_id, pr.market_code, pr.game_date
    """

    total = db.execute(
        text(f"SELECT COUNT(*) FROM ({dedup_sql}) pr"), params
    ).scalar_one()

    rows = db.execute(
        text(
            f"""
            WITH next_game AS ({dedup_sql})
            SELECT pr.player_id, pr.player_name, pr.position, pr.team, pr.opponent,
                   pr.game_id, pr.game_date, pr.market_code, pr.projection,
                   pr.p10, pr.p25, pr.p50, pr.p75, pr.p90,
                   pr.model_name, pr.depth_rank, pr.is_starter,
                   p.id AS app_player_id, p.headshot,
                   -- When this player last actually played.
                   --
                   -- A projection is only as current as the games behind it,
                   -- and four listed starters have not played since January
                   -- 2025: a fullback who is the only fullback on his depth
                   -- chart, a couple of fringe backs, a depth receiver. The
                   -- depth chart is right about them and the number is built on
                   -- twenty month old football, which the page had no way to
                   -- say.
                   lg.last_game
            FROM next_game pr
            LEFT JOIN players p ON p.external_id = pr.player_id
            LEFT JOIN (
                SELECT player_id, max(game_date) AS last_game
                FROM player_game_stats_app GROUP BY player_id
            ) lg ON lg.player_id = pr.player_id
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


@router.get("/projections/games")
def list_projection_games(db: Session = Depends(get_db)):
    """The games on the current projection slate, for a game picker.

    One row per game, both teams in a fixed order, soonest first. Read from the
    projections themselves rather than the schedule, so a game appears here
    exactly when there is something to show for it.
    """
    rows = db.execute(
        text(
            """
            SELECT game_id, MIN(game_date) AS game_date,
                   MIN(LEAST(team, opponent))    AS team_a,
                   MIN(GREATEST(team, opponent)) AS team_b,
                   COUNT(DISTINCT player_id)     AS players
            FROM player_projections
            WHERE game_id IS NOT NULL
            GROUP BY game_id
            ORDER BY MIN(game_date), MIN(LEAST(team, opponent))
            """
        )
    ).mappings().all()
    return {
        "ok": True,
        "games": [
            {**dict(r), "game_date": str(r["game_date"]) if r["game_date"] else None}
            for r in rows
        ],
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
            SELECT DISTINCT ON (market_code)
                   market_code, game_date, opponent, projection,
                   p10, p25, p50, p75, p90, model_name, depth_rank
            FROM player_projections
            WHERE player_id = :ext
            ORDER BY market_code, game_date
            """
        ),
        {"ext": player["external_id"]},
    ).mappings().all()

    return {"ok": True, "player_id": player_id, "projections": [dict(r) for r in rows]}
