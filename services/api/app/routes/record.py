"""Track record endpoints: how the picks have actually done, by season.

The player page used to show a single lifetime hit rate built from 283 graded
picks that all came from one afternoon in December 2023. That is not a track
record, it is an anecdote, and a "50% hit rate, 1W-1L" badge on a player profile
is actively misleading.

`backfill_track_record.py` now reconstructs every season we hold closing lines
for, using models refit on strictly earlier seasons, and tags those rows
`source='backtest'`. Live picks stay `source='live'`. These endpoints keep the
two separable everywhere, because a backtested record and a real-money record
are not the same claim and the site should never blur them.

Every response reports the sample size next to the rate. A 100% hit rate on two
picks is noise, and a number without its `n` invites exactly that mistake.
"""

from fastapi import APIRouter, Query
from sqlalchemy import text

from ..db import engine

router = APIRouter()

# A rate computed on a handful of picks is not worth showing on a leaderboard.
# The market moves a few points a season; ten picks cannot resolve that.
MIN_PICKS_LEADERBOARD = 20


def _rows(sql: str, params: dict) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


@router.get("/record/seasons")
def record_seasons(source: str = Query("all", pattern="^(all|live|backtest)$")):
    """Headline numbers per season, plus a combined row.

    ROI is in units per 1-unit stake at the price actually offered, which is the
    only honest summary: a 55% hit rate at -200 loses money and a 48% hit rate at
    +150 makes it, so hit rate alone says very little.
    """
    where = "hit IS NOT NULL" + ("" if source == "all" else " AND source = :src")
    rows = _rows(f"""
        SELECT season,
               source,
               COUNT(*) AS picks,
               COUNT(DISTINCT player_id) AS players,
               AVG(CASE WHEN hit THEN 1.0 ELSE 0 END) AS hit_rate,
               AVG(win_prob) AS model_predicted,
               AVG(expected_value) AS avg_ev,
               SUM(CASE
                     WHEN hit AND price_american > 0 THEN price_american / 100.0
                     WHEN hit AND price_american < 0 THEN 100.0 / (-price_american)
                     WHEN hit THEN 0.91
                     ELSE -1.0
                   END) AS units
        FROM prop_edge_results
        WHERE {where} AND season IS NOT NULL
        GROUP BY season, source
        ORDER BY season DESC
    """, {"src": source})
    for r in rows:
        r["roi"] = (r["units"] / r["picks"]) if r["picks"] else None
    return {"ok": True, "seasons": rows}


@router.get("/record/by_market")
def record_by_market(
    season: int | None = None,
    source: str = Query("all", pattern="^(all|live|backtest)$"),
):
    """Which markets the model is actually good at."""
    where = ["hit IS NOT NULL"]
    params: dict = {}
    if season:
        where.append("season = :season")
        params["season"] = season
    if source != "all":
        where.append("source = :src")
        params["src"] = source
    rows = _rows(f"""
        SELECT market_code,
               COUNT(*) AS picks,
               AVG(CASE WHEN hit THEN 1.0 ELSE 0 END) AS hit_rate,
               AVG(win_prob) AS model_predicted,
               SUM(CASE
                     WHEN hit AND price_american > 0 THEN price_american / 100.0
                     WHEN hit AND price_american < 0 THEN 100.0 / (-price_american)
                     WHEN hit THEN 0.91
                     ELSE -1.0
                   END) AS units
        FROM prop_edge_results
        WHERE {' AND '.join(where)}
        GROUP BY market_code
        ORDER BY picks DESC
    """, params)
    for r in rows:
        r["roi"] = (r["units"] / r["picks"]) if r["picks"] else None
    return {"ok": True, "markets": rows}


@router.get("/record/by_tier")
def record_by_tier(
    season: int | None = None,
    source: str = Query("all", pattern="^(all|live|backtest)$"),
):
    """Does a higher tier actually win more often?

    This is the single most useful number on the site for judging whether the
    tiers mean anything. If "elite" does not beat "small" the labels are
    decoration.
    """
    where = ["hit IS NOT NULL"]
    params: dict = {}
    if season:
        where.append("season = :season")
        params["season"] = season
    if source != "all":
        where.append("source = :src")
        params["src"] = source
    rows = _rows(f"""
        SELECT edge_tier,
               COUNT(*) AS picks,
               AVG(CASE WHEN hit THEN 1.0 ELSE 0 END) AS hit_rate,
               AVG(win_prob) AS model_predicted,
               SUM(CASE
                     WHEN hit AND price_american > 0 THEN price_american / 100.0
                     WHEN hit AND price_american < 0 THEN 100.0 / (-price_american)
                     WHEN hit THEN 0.91
                     ELSE -1.0
                   END) AS units
        FROM prop_edge_results
        WHERE {' AND '.join(where)}
        GROUP BY edge_tier
    """, params)
    order = {"elite": 0, "strong": 1, "medium": 2, "small": 3}
    for r in rows:
        r["roi"] = (r["units"] / r["picks"]) if r["picks"] else None
    rows.sort(key=lambda r: order.get(r["edge_tier"], 9))
    return {"ok": True, "tiers": rows}


@router.get("/record/leaders")
def record_leaders(
    season: int | None = None,
    source: str = Query("all", pattern="^(all|live|backtest)$"),
    min_picks: int = Query(MIN_PICKS_LEADERBOARD, ge=5, le=500),
    limit: int = Query(10, ge=1, le=50),
    direction: str = Query("best", pattern="^(best|worst)$"),
):
    """Players the model reads best, and worst.

    The worst list is deliberately available. Knowing where the model is
    reliably wrong is worth as much as knowing where it is right, and hiding it
    would make the page a highlight reel.
    """
    where = ["r.hit IS NOT NULL"]
    params: dict = {"min_picks": min_picks, "limit": limit}
    if season:
        where.append("r.season = :season")
        params["season"] = season
    if source != "all":
        where.append("r.source = :src")
        params["src"] = source
    rows = _rows(f"""
        SELECT r.player_id,
               MAX(r.player_name) AS player_name,
               MAX(p.id) AS app_player_id,
               MAX(p.position) AS position,
               MAX(p.team) AS team,
               -- 142 skill players have duplicate rows and some of the copies
               -- carry a NULL headshot, so MAX() alone would sometimes hand
               -- back the empty one. Filtering nulls out first guarantees a
               -- picture whenever any copy of the player has one.
               MAX(p.headshot) FILTER (WHERE p.headshot IS NOT NULL) AS headshot,
               COUNT(*) AS picks,
               AVG(CASE WHEN r.hit THEN 1.0 ELSE 0 END) AS hit_rate,
               SUM(CASE
                     WHEN r.hit AND r.price_american > 0 THEN r.price_american / 100.0
                     WHEN r.hit AND r.price_american < 0 THEN 100.0 / (-r.price_american)
                     WHEN r.hit THEN 0.91
                     ELSE -1.0
                   END) AS units
        FROM prop_edge_results r
        LEFT JOIN players p ON p.external_id = r.player_id
        WHERE {' AND '.join(where)}
        GROUP BY r.player_id
        HAVING COUNT(*) >= :min_picks
        ORDER BY AVG(CASE WHEN r.hit THEN 1.0 ELSE 0 END)
                 {'DESC' if direction == 'best' else 'ASC'},
                 COUNT(*) DESC
        LIMIT :limit
    """, params)
    for r in rows:
        r["roi"] = (r["units"] / r["picks"]) if r["picks"] else None
    return {"ok": True, "direction": direction, "min_picks": min_picks,
            "leaders": rows}


@router.get("/record/calibration")
def record_calibration(
    season: int | None = None,
    source: str = Query("all", pattern="^(all|live|backtest)$"),
):
    """Predicted probability against what actually happened, in buckets.

    This is the plot that tells you whether to believe any number on the site.
    A model claiming 70% should win about 70% of those picks; if it wins 55%,
    every tier and every EV figure downstream is inflated by the same gap.
    """
    where = ["hit IS NOT NULL", "win_prob IS NOT NULL"]
    params: dict = {}
    if season:
        where.append("season = :season")
        params["season"] = season
    if source != "all":
        where.append("source = :src")
        params["src"] = source
    rows = _rows(f"""
        SELECT width_bucket(win_prob, 0.5, 1.0, 10) AS bucket,
               COUNT(*) AS picks,
               AVG(win_prob) AS predicted,
               AVG(CASE WHEN hit THEN 1.0 ELSE 0 END) AS actual
        FROM prop_edge_results
        WHERE {' AND '.join(where)}
        GROUP BY bucket
        HAVING COUNT(*) >= 10
        ORDER BY bucket
    """, params)
    return {"ok": True, "buckets": rows}
