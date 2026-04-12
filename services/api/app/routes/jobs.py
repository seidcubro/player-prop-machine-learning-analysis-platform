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
        raise HTTPException(
            status_code=400, detail=f"Unsafe SQL identifier: {name}")
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
        raise HTTPException(
            status_code=404, detail=f"Unknown market_code: {market_code}")

    return m


def _get_safe_upstream_markets(db: Session, market_code: str):
    """
    Controlled upstream feature graph.
    Keep this conservative to avoid leakage and circular logic.
    """
    allowed_by_market = {
        # receiving
        "recs": [],
        "rec_yds": ["recs"],
        "rec_td": ["recs"],

        # rushing
        "rush_att": [],
        "rush_yds": ["rush_att"],
        "rush_td": ["rush_att"],

        # passing
        "pass_att": [],
        "pass_completions": ["pass_att"],
        "pass_yds": ["pass_att"],
        "pass_td": ["pass_att", "pass_completions"],
        "pass_ints": ["pass_att", "pass_completions"],

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
        raise HTTPException(
            status_code=400, detail=f"Market is inactive: {market_code}")
    if not m["train_enabled"]:
        raise HTTPException(
            status_code=400, detail=f"Training disabled for market: {market_code}")
    if m["scope"] != "player":
        raise HTTPException(
            status_code=400, detail=f"Only player-scoped markets are supported here. Got: {m['scope']}")
    if m["target_kind"] != "regression":
        raise HTTPException(
            status_code=400, detail=f"Only regression markets are supported here. Got: {m['target_kind']}")

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
        WITH team_rush_offense AS (
            SELECT
                team,
                game_date,
                SUM(COALESCE(carries, 0))::float8 AS team_rush_attempts
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
              AND team IS NOT NULL
            GROUP BY team, game_date
        ),
        team_rush_defense AS (
            SELECT
                opponent AS defense_team,
                game_date,
                SUM(COALESCE(carries, 0))::float8 AS opp_carries_allowed,
                SUM(COALESCE(rushing_yards, 0))::float8 AS opp_rush_yards_allowed
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
              AND opponent IS NOT NULL
            GROUP BY opponent, game_date
        ),
        team_pass_offense AS (
            SELECT
                team,
                game_date,
                SUM(COALESCE(attempts, 0))::float8 AS team_pass_attempts_calc
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
              AND team IS NOT NULL
            GROUP BY team, game_date
        ),
        team_pass_defense AS (
            SELECT
                opponent AS defense_team,
                game_date,
                SUM(COALESCE(attempts, 0))::float8 AS opp_pass_attempts_allowed,
                SUM(COALESCE(passing_yards, 0))::float8 AS opp_pass_yards_allowed
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
              AND opponent IS NOT NULL
            GROUP BY opponent, game_date
        )
        SELECT
            pgs.player_id,
            pgs.team,
            pgs.position,
            pgs.game_date,
            pgs.opponent,

            COALESCE(pgs.{stat_field}, 0)::float8 AS y,
            COALESCE(pgs.targets, 0)::float8 AS targets,
            COALESCE(pgs.receptions, 0)::float8 AS receptions,
            COALESCE(pgs.receiving_yards, 0)::float8 AS receiving_yards,
            COALESCE(pgs.receiving_tds, 0)::float8 AS receiving_tds,
            COALESCE(pgs.carries, 0)::float8 AS carries,
            COALESCE(pgs.rushing_yards, 0)::float8 AS rushing_yards,
            COALESCE(pgs.rushing_tds, 0)::float8 AS rushing_tds,
            COALESCE(pgs.attempts, 0)::float8 AS pass_attempts,
            COALESCE(pgs.completions, 0)::float8 AS completions,
            COALESCE(pgs.passing_yards, 0)::float8 AS passing_yards,
            COALESCE(pgs.passing_tds, 0)::float8 AS passing_tds,
            COALESCE(tpo.team_pass_attempts_calc, 0)::float8 AS team_pass_attempts_calc,
            COALESCE(tpd.opp_pass_attempts_allowed, 0)::float8 AS opp_pass_attempts_allowed,
            COALESCE(tpd.opp_pass_yards_allowed, 0)::float8 AS opp_pass_yards_allowed,
            COALESCE(top.team_pass_attempts, 0)::float8 AS team_pass_attempts,
            COALESCE(tdr.opp_rec_yds_allowed, 0)::float8 AS opp_rec_yds_allowed,
            COALESCE(tdr.opp_targets_allowed, 0)::float8 AS opp_targets_allowed,
            COALESCE(tdrr.opp_rec_yds_allowed_rolling, 0)::float8 AS opp_rec_yds_allowed_rolling,
            COALESCE(tdrr.opp_targets_allowed_rolling, 0)::float8 AS opp_targets_allowed_rolling,

            COALESCE(tro.team_rush_attempts, 0)::float8 AS team_rush_attempts,
            COALESCE(trd.opp_rush_yards_allowed, 0)::float8 AS opp_rush_yards_allowed,
            COALESCE(trd.opp_carries_allowed, 0)::float8 AS opp_carries_allowed

            {select_upstream_sql}
        FROM player_game_stats_app pgs
        LEFT JOIN team_pass_offense tpo
            ON tpo.team = pgs.team
           AND tpo.game_date = pgs.game_date

        LEFT JOIN team_pass_defense tpd
            ON tpd.defense_team = pgs.opponent
           AND tpd.game_date = pgs.game_date
        LEFT JOIN team_offense_pass top
            ON top.team = pgs.team
           AND top.game_date = pgs.game_date
        LEFT JOIN team_defense_rec tdr
            ON tdr.team = pgs.opponent
        LEFT JOIN team_defense_rec_rolling tdrr
            ON tdrr.defense_team = pgs.opponent
           AND tdrr.game_date = pgs.game_date
        LEFT JOIN team_rush_offense tro
            ON tro.team = pgs.team
           AND tro.game_date = pgs.game_date
        LEFT JOIN team_rush_defense trd
            ON trd.defense_team = pgs.opponent
           AND trd.game_date = pgs.game_date
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
            aux_mean,
            aux_trend,
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
            :aux_mean,
            :aux_trend,
            CAST(:extra_features AS jsonb)
          )
        ON CONFLICT (player_id, market_id, as_of_game_date, opponent, lookback)
        DO UPDATE SET
          mean = EXCLUDED.mean,
          stddev = EXCLUDED.stddev,
          weighted_mean = EXCLUDED.weighted_mean,
          trend = EXCLUDED.trend,
          aux_mean = EXCLUDED.aux_mean,
          aux_trend = EXCLUDED.aux_trend,
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

            extra_features = {}

            for code, _col in upstream_cols:
                vals = [float(g.get(code, 0.0) or 0.0) for g in window_games]
                if vals:
                    extra_features[f"{code}_mean"] = _mean(vals)
                    extra_features[f"{code}_trend"] = _trend_slope(vals)

            if feature_family == "rushing":
                carries_window = [float(g.get("carries", 0.0) or 0.0)
                                        for g in window_games]
                rush_yards_window = [
                    float(g.get("rushing_yards", 0.0) or 0.0) for g in window_games]

                if carries_window:
                    extra_features["carries_weighted_mean"] = _weighted_mean_recent(
                        carries_window)

                team_rush_window = [
                    float(g.get("team_rush_attempts", 0.0) or 0.0) for g in window_games]
                team_rush_window_nonzero = [
                    v for v in team_rush_window if v > 0]

                if team_rush_window_nonzero:
                    extra_features["team_rush_attempts"] = _mean(
                        team_rush_window_nonzero)
                    extra_features["team_rush_attempts_trend"] = _trend_slope(
                        team_rush_window_nonzero)

                if carries_window and team_rush_window:
                    cs_vals = []
                    for c, tr_team in zip(carries_window, team_rush_window):
                        if tr_team > 0:
                            cs_vals.append(c / tr_team)

                    if cs_vals:
                        extra_features["carry_share_mean"] = _mean(cs_vals)
                        extra_features["carry_share_trend"] = _trend_slope(
                            cs_vals)

                if market_code == "rush_yds" and carries_window and rush_yards_window:
                    ypc_vals = []
                    for y, c in zip(rush_yards_window, carries_window):
                        if c > 0:
                            ypc_vals.append(y / c)

                    if ypc_vals:
                        extra_features["yards_per_carry_mean"] = _mean(ypc_vals)
                        extra_features["yards_per_carry_trend"] = _trend_slope(ypc_vals)

                if market_code == "rush_td" and carries_window:
                    td_window = [float(g.get("rushing_tds", 0.0) or 0.0) for g in window_games]

                    td_rate_vals = []
                    for td, c in zip(td_window, carries_window):
                        if c > 0:
                            td_rate_vals.append(td / c)

                    if td_rate_vals:
                        extra_features["rush_td_rate_mean"] = _mean(td_rate_vals)
                        extra_features["rush_td_rate_trend"] = _trend_slope(td_rate_vals)
                opp_rush_vals = [
                    float(g.get("opp_rush_yards_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_carry_vals = [
                    float(g.get("opp_carries_allowed", 0.0) or 0.0)
                    for g in window_games
                ]

                opp_rush_vals_nonzero = [v for v in opp_rush_vals if v > 0]
                opp_carry_vals_nonzero = [v for v in opp_carry_vals if v > 0]

                if opp_rush_vals_nonzero:
                    extra_features["opp_rush_yards_allowed"] = _mean(
                        opp_rush_vals_nonzero)

                if opp_carry_vals_nonzero:
                    extra_features["opp_carries_allowed"] = _mean(
                        opp_carry_vals_nonzero)

                if opp_rush_vals and opp_carry_vals:
                    opp_ypc_vals = []
                    for oy, oc in zip(opp_rush_vals, opp_carry_vals):
                        if oc > 0:
                            opp_ypc_vals.append(oy / oc)

                    if opp_ypc_vals:
                        extra_features["opp_yards_per_carry_allowed"] = _mean(
                            opp_ypc_vals)
                        extra_features["opp_yards_per_carry_trend"] = _trend_slope(
                            opp_ypc_vals)
                        
            if feature_family == "passing":
                pass_window = [float(g.get("pass_attempts", 0.0) or 0.0) for g in window_games]
                completions_window = [float(g.get("completions", 0.0) or 0.0) for g in window_games]
                pass_yds_window = [float(g.get("passing_yards", 0.0) or 0.0) for g in window_games]
                pass_td_window = [float(g.get("passing_tds", 0.0) or 0.0) for g in window_games]

                if pass_window:
                    extra_features["pass_attempts_weighted_mean"] = _weighted_mean_recent(pass_window)

                team_pass_window = [float(g.get("team_pass_attempts_calc", 0.0) or 0.0) for g in window_games]
                team_pass_nonzero = [v for v in team_pass_window if v > 0]

                if team_pass_nonzero:
                    extra_features["team_pass_attempts"] = _mean(team_pass_nonzero)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(team_pass_nonzero)

                if pass_window and team_pass_window:
                    share_vals = []
                    for pa, tp in zip(pass_window, team_pass_window):
                        if tp > 0:
                            share_vals.append(pa / tp)

                    if share_vals:
                        extra_features["pass_share_mean"] = _mean(share_vals)
                        extra_features["pass_share_trend"] = _trend_slope(share_vals)

                if market_code == "pass_completions" and pass_window and completions_window:
                    comp_rate_vals = []
                    for comp, att in zip(completions_window, pass_window):
                        if att > 0:
                            comp_rate_vals.append(comp / att)

                    if comp_rate_vals:
                        extra_features["completion_rate_mean"] = _mean(comp_rate_vals)
                        extra_features["completion_rate_trend"] = _trend_slope(comp_rate_vals)

                if market_code == "pass_yds" and pass_window and pass_yds_window:
                    ypa_vals = []
                    for py, pa in zip(pass_yds_window, pass_window):
                        if pa > 0:
                            ypa_vals.append(py / pa)

                    if ypa_vals:
                        extra_features["yards_per_attempt_mean"] = _mean(ypa_vals)
                        extra_features["yards_per_attempt_trend"] = _trend_slope(ypa_vals)

                if market_code == "pass_td" and pass_window and pass_td_window:
                    td_rate_vals = []
                    for td, att in zip(pass_td_window, pass_window):
                        if att > 0:
                            td_rate_vals.append(td / att)

                    if td_rate_vals:
                        extra_features["pass_td_rate_mean"] = _mean(td_rate_vals)
                        extra_features["pass_td_rate_trend"] = _trend_slope(td_rate_vals)

                opp_pass_vals = [
                    float(g.get("opp_pass_attempts_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_nonzero = [v for v in opp_pass_vals if v > 0]

                if opp_pass_nonzero:
                    extra_features["opp_pass_attempts_allowed"] = _mean(opp_pass_nonzero)
                    extra_features["opp_pass_attempts_trend"] = _trend_slope(opp_pass_nonzero)

                opp_pass_yds_vals = [
                    float(g.get("opp_pass_yards_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_yds_nonzero = [v for v in opp_pass_yds_vals if v > 0]

                if opp_pass_yds_nonzero:
                    extra_features["opp_pass_yards_allowed"] = _mean(opp_pass_yds_nonzero)

                if opp_pass_vals and opp_pass_yds_vals:
                    opp_ypa_vals = []
                    for oy, oa in zip(opp_pass_yds_vals, opp_pass_vals):
                        if oa > 0:
                            opp_ypa_vals.append(oy / oa)

                    if opp_ypa_vals:
                        extra_features["opp_yards_per_attempt_allowed"] = _mean(opp_ypa_vals)
                        extra_features["opp_yards_per_attempt_trend"] = _trend_slope(opp_ypa_vals)

                if pass_window:
                    extra_features["pass_attempts_weighted_mean"] = _weighted_mean_recent(pass_window)

                team_pass_window = [float(g.get("team_pass_attempts_calc", 0.0) or 0.0) for g in window_games]
                team_pass_nonzero = [v for v in team_pass_window if v > 0]

                if team_pass_nonzero:
                    extra_features["team_pass_attempts"] = _mean(team_pass_nonzero)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(team_pass_nonzero)

                if pass_window and team_pass_window:
                    share_vals = []
                    for pa, tp in zip(pass_window, team_pass_window):
                        if tp > 0:
                            share_vals.append(pa / tp)

                    if share_vals:
                        extra_features["pass_share_mean"] = _mean(share_vals)
                        extra_features["pass_share_trend"] = _trend_slope(share_vals)

                if market_code == "pass_completions" and pass_window and completions_window:
                    comp_rate_vals = []
                    for comp, att in zip(completions_window, pass_window):
                        if att > 0:
                            comp_rate_vals.append(comp / att)

                    if comp_rate_vals:
                        extra_features["completion_rate_mean"] = _mean(comp_rate_vals)
                        extra_features["completion_rate_trend"] = _trend_slope(comp_rate_vals)

                if market_code == "pass_yds" and pass_window and pass_yds_window:
                    ypa_vals = []
                    for py, pa in zip(pass_yds_window, pass_window):
                        if pa > 0:
                            ypa_vals.append(py / pa)

                    if ypa_vals:
                        extra_features["yards_per_attempt_mean"] = _mean(ypa_vals)
                        extra_features["yards_per_attempt_trend"] = _trend_slope(ypa_vals)

                opp_pass_vals = [
                    float(g.get("opp_pass_attempts_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_nonzero = [v for v in opp_pass_vals if v > 0]

                if opp_pass_nonzero:
                    extra_features["opp_pass_attempts_allowed"] = _mean(opp_pass_nonzero)
                    extra_features["opp_pass_attempts_trend"] = _trend_slope(opp_pass_nonzero)

                opp_pass_yds_vals = [
                    float(g.get("opp_pass_yards_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_yds_nonzero = [v for v in opp_pass_yds_vals if v > 0]

                if opp_pass_yds_nonzero:
                    extra_features["opp_pass_yards_allowed"] = _mean(opp_pass_yds_nonzero)

                if opp_pass_vals and opp_pass_yds_vals:
                    opp_ypa_vals = []
                    for oy, oa in zip(opp_pass_yds_vals, opp_pass_vals):
                        if oa > 0:
                            opp_ypa_vals.append(oy / oa)

                    if opp_ypa_vals:
                        extra_features["opp_yards_per_attempt_allowed"] = _mean(opp_ypa_vals)
                        extra_features["opp_yards_per_attempt_trend"] = _trend_slope(opp_ypa_vals)

                if pass_window:
                    extra_features["pass_attempts_weighted_mean"] = _weighted_mean_recent(pass_window)

                team_pass_window = [float(g.get("team_pass_attempts_calc", 0.0) or 0.0) for g in window_games]
                team_pass_nonzero = [v for v in team_pass_window if v > 0]

                if team_pass_nonzero:
                    extra_features["team_pass_attempts"] = _mean(team_pass_nonzero)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(team_pass_nonzero)

                if pass_window and team_pass_window:
                    share_vals = []
                    for pa, tp in zip(pass_window, team_pass_window):
                        if tp > 0:
                            share_vals.append(pa / tp)

                    if share_vals:
                        extra_features["pass_share_mean"] = _mean(share_vals)
                        extra_features["pass_share_trend"] = _trend_slope(share_vals)

                if market_code == "pass_yds" and pass_window and pass_yds_window:
                    ypa_vals = []
                    for py, pa in zip(pass_yds_window, pass_window):
                        if pa > 0:
                            ypa_vals.append(py / pa)

                    if ypa_vals:
                        extra_features["yards_per_attempt_mean"] = _mean(ypa_vals)
                        extra_features["yards_per_attempt_trend"] = _trend_slope(ypa_vals)

                opp_pass_vals = [
                    float(g.get("opp_pass_attempts_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_nonzero = [v for v in opp_pass_vals if v > 0]

                if opp_pass_nonzero:
                    extra_features["opp_pass_attempts_allowed"] = _mean(opp_pass_nonzero)
                    extra_features["opp_pass_attempts_trend"] = _trend_slope(opp_pass_nonzero)

                opp_pass_yds_vals = [
                    float(g.get("opp_pass_yards_allowed", 0.0) or 0.0)
                    for g in window_games
                ]
                opp_pass_yds_nonzero = [v for v in opp_pass_yds_vals if v > 0]

                if opp_pass_yds_nonzero:
                    extra_features["opp_pass_yards_allowed"] = _mean(opp_pass_yds_nonzero)

                if opp_pass_vals and opp_pass_yds_vals:
                    opp_ypa_vals = []
                    for oy, oa in zip(opp_pass_yds_vals, opp_pass_vals):
                        if oa > 0:
                            opp_ypa_vals.append(oy / oa)

                    if opp_ypa_vals:
                        extra_features["opp_yards_per_attempt_allowed"] = _mean(opp_ypa_vals)
                        extra_features["opp_yards_per_attempt_trend"] = _trend_slope(opp_ypa_vals)

                if pass_window:
                    extra_features["pass_attempts_weighted_mean"] = _weighted_mean_recent(pass_window)

                team_pass_window = [float(g.get("team_pass_attempts_calc", 0.0) or 0.0) for g in window_games]
                team_pass_nonzero = [v for v in team_pass_window if v > 0]

                if team_pass_nonzero:
                    extra_features["team_pass_attempts"] = _mean(team_pass_nonzero)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(team_pass_nonzero)

                if pass_window and team_pass_window:
                    share_vals = []
                    for pa, tp in zip(pass_window, team_pass_window):
                        if tp > 0:
                            share_vals.append(pa / tp)

                    if share_vals:
                        extra_features["pass_share_mean"] = _mean(share_vals)
                        extra_features["pass_share_trend"] = _trend_slope(share_vals)

                opp_pass_vals = [
                    float(g.get("opp_pass_attempts_allowed", 0.0) or 0.0)
                    for g in window_games
                ]

                opp_pass_nonzero = [v for v in opp_pass_vals if v > 0]

                if opp_pass_nonzero:
                    extra_features["opp_pass_attempts_allowed"] = _mean(opp_pass_nonzero)
                    extra_features["opp_pass_attempts_trend"] = _trend_slope(opp_pass_nonzero)

            if feature_family == "receiving":
                targets_window = [float(g.get("targets", 0.0) or 0.0)
                                  for g in window_games]
                if targets_window:
                    extra_features["targets_weighted_mean"] = _weighted_mean_recent(
                        targets_window)

                if targets_window and market_code == "rec_yds":
                    ypt_vals = [
                        (y / t) if t not in (None, 0, 0.0) else 0.0
                        for y, t in zip(window, targets_window)
                    ]
                    extra_features["yards_per_target_mean"] = _mean(ypt_vals)
                    extra_features["yards_per_target_trend"] = _trend_slope(
                        ypt_vals)

                team_pass_window = [
                    float(g.get("team_pass_attempts", 0.0) or 0.0) for g in window_games
                ]
                if team_pass_window:
                    extra_features["team_pass_attempts"] = _mean(
                        team_pass_window)
                    extra_features["team_pass_attempts_trend"] = _trend_slope(
                        team_pass_window)

                if targets_window and team_pass_window:
                    ts_vals = []
                    for t, tp in zip(targets_window, team_pass_window):
                        if tp > 0:
                            ts_vals.append(t / tp)
                        else:
                            ts_vals.append(0.0)

                    if ts_vals:
                        extra_features["target_share_mean"] = _mean(ts_vals)
                        extra_features["target_share_trend"] = _trend_slope(
                            ts_vals)

                opp_rec_vals = []
                opp_targets_vals = []
                for g in window_games:
                    opp_rec_roll = float(
                        g.get("opp_rec_yds_allowed_rolling", 0.0) or 0.0)
                    opp_rec_base = float(
                        g.get("opp_rec_yds_allowed", 0.0) or 0.0)
                    opp_targets_roll = float(
                        g.get("opp_targets_allowed_rolling", 0.0) or 0.0)
                    opp_targets_base = float(
                        g.get("opp_targets_allowed", 0.0) or 0.0)

                    opp_rec_vals.append(
                        opp_rec_roll if opp_rec_roll > 0 else opp_rec_base)
                    opp_targets_vals.append(
                        opp_targets_roll if opp_targets_roll > 0 else opp_targets_base)

                opp_adj = []
                for opp_y, team_pass in zip(opp_rec_vals, team_pass_window):
                    if team_pass > 0:
                        opp_adj.append(opp_y / team_pass)
                    else:
                        opp_adj.append(0.0)

                if opp_adj:
                    extra_features["opp_rec_yds_per_attempt"] = _mean(opp_adj)
                    extra_features["opp_rec_yds_per_attempt_trend"] = _trend_slope(
                        opp_adj)

                opp_tgt_adj = []
                for opp_t, team_pass in zip(opp_targets_vals, team_pass_window):
                    if team_pass > 0:
                        opp_tgt_adj.append(opp_t / team_pass)
                    else:
                        opp_tgt_adj.append(0.0)

                if opp_tgt_adj:
                    extra_features["opp_target_rate_allowed"] = _mean(
                        opp_tgt_adj)
                    extra_features["opp_target_rate_trend"] = _trend_slope(
                        opp_tgt_adj)
                    
                if market_code == "rec_td":
                    recs_window = [float(g.get("receptions", 0.0) or 0.0) for g in window_games]
                    td_window = [float(g.get("receiving_tds", 0.0) or 0.0) for g in window_games]

                    td_rate_vals = []
                    for td, rec in zip(td_window, recs_window):
                        if rec > 0:
                            td_rate_vals.append(td / rec)

                    if td_rate_vals:
                        extra_features["rec_td_rate_mean"] = _mean(td_rate_vals)
                        extra_features["rec_td_rate_trend"] = _trend_slope(td_rate_vals)

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
                    "aux_mean": aux_mean,
                    "aux_trend": aux_trend,
                    "extra_features": json.dumps(extra_features),
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
        raise HTTPException(
            status_code=400, detail=f"Market is inactive: {market_code}")
    if not m["train_enabled"]:
        raise HTTPException(
            status_code=400, detail=f"Training disabled for market: {market_code}")
    if m["scope"] != "player":
        raise HTTPException(
            status_code=400, detail=f"Only player-scoped markets are supported here. Got: {m['scope']}")
    if m["target_kind"] != "regression":
        raise HTTPException(
            status_code=400, detail=f"Only regression markets are supported here. Got: {m['target_kind']}")

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
