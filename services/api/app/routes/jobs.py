"""Job-related API routes.

Market-driven feature building and label attachment.
Uses the market registry to determine:
- target stat_field
- eligible positions
- feature family
- safe upstream features

This version stores cross-market upstream features in player_market_features.extra_features JSONB.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
import math
import re
import json

from ..db import get_db

router = APIRouter()

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mean(vals):
    return sum(vals) / len(vals)


def _stddev_pop(vals):
    m = _mean(vals)
    return math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))


def _weighted_mean_recent(vals):
    n = len(vals)
    weights = list(range(1, n + 1))
    return sum(v * w for v, w in zip(vals, weights)) / sum(weights)


def _trend_slope(vals):
    n = len(vals)
    xs = list(range(1, n + 1))
    xbar = sum(xs) / n
    ybar = _mean(vals)
    num = sum((x - xbar) * (y - ybar) for x, y in zip(xs, vals))
    den = sum((x - xbar) ** 2 for x in xs)
    return 0.0 if den == 0 else (num / den)


def _safe_identifier(name: str) -> str:
    if not name or not _IDENTIFIER_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Unsafe SQL identifier: {name}")
    return name


def _as_text_array(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):
            inner = s[1:-1].strip()
            if not inner:
                return []
            return [part.strip().strip('"') for part in inner.split(",")]
        return [s]
    return [str(value)]


def _column_exists(db: Session, table_name: str, column_name: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).first()
    return row is not None


def _get_market(db: Session, market_code: str):
    m = db.execute(
        text(
            """
            SELECT
              id,
              code,
              name,
              stat_field,
              scope,
              target_kind,
              entity_key,
              eligible_positions,
              is_active,
              train_enabled,
              predict_enabled,
              feature_family,
              can_be_upstream_feature,
              is_synthetic_target
            FROM prop_markets
            WHERE code = :code
            """
        ),
        {"code": market_code},
    ).mappings().first()

    if not m:
        raise HTTPException(status_code=404, detail=f"Unknown market_code: {market_code}")

    return m


def _get_safe_upstream_markets(db: Session, market_code: str):
    """
    Controlled upstream feature graph.
    Keep this conservative to avoid leakage and circular logic.
    """
    allowed_by_market = {
        # receiving
        "rec_yds": ["targets", "recs"],
        "recs": ["targets"],
        "rec_tds": ["targets", "recs"],

        # passing
        "pass_yds": ["pass_attempts", "pass_completions"],
        "pass_tds": ["pass_attempts", "pass_completions"],
        "pass_ints": ["pass_attempts", "pass_completions"],
        "pass_completions": ["pass_attempts"],

        # rushing
        "rush_yds": ["carries"],
        "rush_tds": ["carries"],

        # others isolated for now
        "carries": [],
        "targets": [],
        "tackles_solo": [],
        "tackles_combined": [],
        "sacks": [],
        "def_ints": [],
        "pass_defended": [],
        "qb_hits": [],
        "tfl": [],
        "forced_fumbles": [],
        "fg_made": [],
        "fg_att": [],
        "fg_long": [],
        "xp_made": [],
        "punts": [],
        "punt_yds": [],
    }

    allowed_codes = allowed_by_market.get(market_code, [])
    if not allowed_codes:
        return []

    rows = db.execute(
        text(
            """
            SELECT code, stat_field
            FROM prop_markets
            WHERE code = ANY(:codes)
              AND can_be_upstream_feature = TRUE
              AND is_active = TRUE
            ORDER BY code
            """
        ),
        {"codes": allowed_codes},
    ).mappings().all()

    safe = []
    for r in rows:
        col = _safe_identifier(str(r["stat_field"]))
        if _column_exists(db, "player_game_stats_app", col):
            safe.append((str(r["code"]), col))

    return safe


@router.post("/jobs/build_features")
def build_features(
    market_code: str,
    lookback: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    m = _get_market(db, market_code)

    if not m["is_active"]:
        raise HTTPException(status_code=400, detail=f"Market is inactive: {market_code}")
    if not m["train_enabled"]:
        raise HTTPException(status_code=400, detail=f"Training disabled for market: {market_code}")
    if m["scope"] != "player":
        raise HTTPException(status_code=400, detail=f"Only player-scoped markets are supported here. Got: {m['scope']}")
    if m["target_kind"] != "regression":
        raise HTTPException(status_code=400, detail=f"Only regression markets are supported here. Got: {m['target_kind']}")

    stat_field = _safe_identifier(str(m["stat_field"]))
    if not _column_exists(db, "player_game_stats_app", stat_field):
        raise HTTPException(
            status_code=400,
            detail=f"stat_field '{stat_field}' does not exist in player_game_stats_app",
        )

    eligible_positions = set(_as_text_array(m["eligible_positions"]))
    feature_family = str(m["feature_family"] or "").strip().lower()
    upstream_cols = _get_safe_upstream_markets(db, market_code)

    select_upstream_sql = ""
    for code, col in upstream_cols:
        alias = _safe_identifier(code)
        select_upstream_sql += f", COALESCE(pgs.{col}, 0)::float8 AS {alias}"

    sql = f"""
        SELECT
          pgs.player_id,
          pgs.position,
          pgs.game_date,
          pgs.opponent,
          COALESCE(pgs.{stat_field}, 0)::float8 AS y
          {select_upstream_sql}
        FROM player_game_stats_app pgs
        WHERE pgs.game_date IS NOT NULL
          AND pgs.opponent IS NOT NULL
        ORDER BY pgs.player_id, pgs.game_date
    """
    rows = db.execute(text(sql)).mappings().all()

    by_player = {}
    for r in rows:
        pos = r["position"]
        if eligible_positions and pos not in eligible_positions:
            continue
        by_player.setdefault(r["player_id"], []).append(r)

    upsert_sql = text(
        """
        INSERT INTO player_market_features
          (
            player_id,
            market_id,
            as_of_game_date,
            opponent,
            lookback,
            mean,
            stddev,
            weighted_mean,
            trend,
            recs_mean,
            recs_trend,
            extra_features
          )
        VALUES
          (
            :player_id,
            :market_id,
            :as_of_game_date,
            :opponent,
            :lookback,
            :mean,
            :stddev,
            :weighted_mean,
            :trend,
            :recs_mean,
            :recs_trend,
            CAST(:extra_features AS jsonb)
          )
        ON CONFLICT (player_id, market_id, as_of_game_date, opponent, lookback)
        DO UPDATE SET
          mean = EXCLUDED.mean,
          stddev = EXCLUDED.stddev,
          weighted_mean = EXCLUDED.weighted_mean,
          trend = EXCLUDED.trend,
          recs_mean = EXCLUDED.recs_mean,
          recs_trend = EXCLUDED.recs_trend,
          extra_features = EXCLUDED.extra_features
        """
    )

    upserts = 0

    for player_id, games in by_player.items():
        ys = [float(g["y"] or 0.0) for g in games]

        for i in range(len(games)):
            if i < lookback:
                continue

            window_games = games[i - lookback:i]
            window = ys[i - lookback:i]

            mu = _mean(window)
            sd = _stddev_pop(window)
            wmu = _weighted_mean_recent(window)
            tr = _trend_slope(window)

            # keep the old columns for compatibility with current train.py
            aux_mean = None
            aux_trend = None

            if feature_family == "receiving":
                aux_window = [
                    float(g.get("recs", g.get("receptions", 0.0)) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            elif feature_family == "rushing":
                aux_window = [
                    float(g.get("carries", 0.0) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            elif feature_family == "passing":
                aux_window = [
                    float(g.get("pass_attempts", 0.0) or 0.0)
                    for g in window_games
                ]
                if aux_window:
                    aux_mean = _mean(aux_window)
                    aux_trend = _trend_slope(aux_window)

            upstream_features = {}
            for code, _col in upstream_cols:
                vals = [float(g.get(code, 0.0) or 0.0) for g in window_games]
                if vals:
                    upstream_features[f"{code}_mean"] = _mean(vals)
                    upstream_features[f"{code}_trend"] = _trend_slope(vals)

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
                    "recs_mean": aux_mean,
                    "recs_trend": aux_trend,
                    "extra_features": json.dumps(upstream_features),
                },
            )
            upserts += 1

    db.commit()

    return {
        "ok": True,
        "market_code": market_code,
        "market_id": m["id"],
        "stat_field": stat_field,
        "feature_family": feature_family,
        "lookback": lookback,
        "eligible_positions": sorted(list(eligible_positions)),
        "upstream_features_used": [code for code, _ in upstream_cols],
        "upserts": upserts,
    }


@router.post("/jobs/attach_labels")
def attach_labels(
    market_code: str,
    db: Session = Depends(get_db),
):
    m = _get_market(db, market_code)

    if not m["is_active"]:
        raise HTTPException(status_code=400, detail=f"Market is inactive: {market_code}")
    if not m["train_enabled"]:
        raise HTTPException(status_code=400, detail=f"Training disabled for market: {market_code}")
    if m["scope"] != "player":
        raise HTTPException(status_code=400, detail=f"Only player-scoped markets are supported here. Got: {m['scope']}")
    if m["target_kind"] != "regression":
        raise HTTPException(status_code=400, detail=f"Only regression markets are supported here. Got: {m['target_kind']}")

    stat_field = _safe_identifier(str(m["stat_field"]))
    if not _column_exists(db, "player_game_stats_app", stat_field):
        raise HTTPException(
            status_code=400,
            detail=f"stat_field '{stat_field}' does not exist in player_game_stats_app",
        )

    sql = f"""
        UPDATE player_market_features pmf
        SET label_actual = pgs.{stat_field}::float8
        FROM player_game_stats_app pgs
        WHERE pmf.player_id = pgs.player_id
          AND pmf.as_of_game_date = pgs.game_date
          AND pmf.opponent = pgs.opponent
          AND pmf.market_id = :market_id
    """

    res = db.execute(text(sql), {"market_id": m["id"]})
    db.commit()

    return {
        "ok": True,
        "market_code": market_code,
        "market_id": m["id"],
        "stat_field": stat_field,
        "updated": res.rowcount,
    }
