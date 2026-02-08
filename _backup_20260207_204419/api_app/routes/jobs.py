from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import math

from ..db import get_db

router = APIRouter()

_ALLOWED_STAT_FIELDS = {
    "receiving_yards",
    "receptions",
    "rushing_yards",
    "rush_attempts",
    "passing_yards",
    "passing_tds",
    "touchdowns",
}

def _mean(vals):
    return sum(vals) / len(vals)

def _stddev_pop(vals):
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))

def _weighted_mean_recent(vals):
    # weights 1..N (more weight to most recent)
    n = len(vals)
    weights = list(range(1, n + 1))
    return sum(v * w for v, w in zip(vals, weights)) / sum(weights)

def _trend_slope(vals):
    # simple linear regression slope for x = 1..N
    n = len(vals)
    xs = list(range(1, n + 1))
    xbar = sum(xs) / n
    ybar = _mean(vals)
    num = sum((x - xbar) * (y - ybar) for x, y in zip(xs, vals))
    den = sum((x - xbar) ** 2 for x in xs)
    return 0.0 if den == 0 else (num / den)


@router.post("/jobs/build_features")
def build_features(
    market_code: str,
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    m = db.execute(
        text("SELECT id, code, stat_field FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()

    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")

    stat_field = m["stat_field"]
    if stat_field not in _ALLOWED_STAT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported stat_field for market {market_code}: {stat_field}",
        )

    # pull all game stats ordered by player/date
    # (safe dynamic column name due to allowlist)
    sql = f"""
        SELECT player_id, game_date, opponent, pgs.{stat_field}::float8 AS y
        FROM player_game_stats pgs
        ORDER BY player_id, game_date
    """
    rows = db.execute(text(sql)).mappings().all()

    # group by player
    by_player = {}
    for r in rows:
        by_player.setdefault(r["player_id"], []).append(r)

    upsert_sql = text("""
        INSERT INTO player_market_features
          (player_id, market_id, as_of_game_date, opponent, lookback, mean, stddev, weighted_mean, trend)
        VALUES
          (:player_id, :market_id, :as_of_game_date, :opponent, :lookback, :mean, :stddev, :weighted_mean, :trend)
        ON CONFLICT (player_id, market_id, as_of_game_date, opponent, lookback)
        DO UPDATE SET
          mean = EXCLUDED.mean,
          stddev = EXCLUDED.stddev,
          weighted_mean = EXCLUDED.weighted_mean,
          trend = EXCLUDED.trend
    """)

    upserts = 0
    for player_id, games in by_player.items():
        ys = [g["y"] for g in games]
        for i in range(len(games)):
            if i < lookback:
                continue  # need prior N games
            window = ys[i - lookback:i]  # prior N games only
            mu = _mean(window)
            sd = _stddev_pop(window)
            wmu = _weighted_mean_recent(window)
            tr = _trend_slope(window)

            db.execute(
                upsert_sql,
                {
                    "player_id": player_id,
                    "market_id": m["id"],
                    "as_of_game_date": games[i]["game_date"],
                    "opponent": games[i]["opponent"],
                    "lookback": lookback,
                    "mean": mu,
                    "stddev": sd,
                    "weighted_mean": wmu,
                    "trend": tr,
                },
            )
            upserts += 1

    db.commit()
    return {"ok": True, "market_code": market_code, "market_id": m["id"], "lookback": lookback, "upserts": upserts}


@router.post("/jobs/attach_labels")
def attach_labels(market_code: str, db: Session = Depends(get_db)):
    m = db.execute(
        text("SELECT id, code, stat_field FROM prop_markets WHERE code = :code"),
        {"code": market_code},
    ).mappings().first()

    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")

    stat_field = m["stat_field"]
    if stat_field not in _ALLOWED_STAT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported stat_field for market {market_code}: {stat_field}",
        )

    sql = f"""
        UPDATE player_market_features pmf
        SET label_actual = pgs.{stat_field}
        FROM player_game_stats pgs
        WHERE pmf.player_id = pgs.player_id
          AND pmf.as_of_game_date = pgs.game_date
          AND pmf.opponent = pgs.opponent
          AND pmf.market_id = :market_id
    """

    res = db.execute(text(sql), {"market_id": m["id"]})
    db.commit()

    return {"ok": True, "market_code": market_code, "market_id": m["id"], "updated": res.rowcount}
