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


def _median(vals):
    ordered = sorted(vals)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _trimmed_mean(vals):
    """Mean of the window with its single highest value dropped.

    At lookback=5 one blow-up game moves `mean` and `weighted_mean` far more
    than the player's true level -- the T.J. Hockenson case, where a 134-yard
    game pulled a 43.5-line player's window mean to ~73. This gives the model
    an outlier-free view of the same window alongside the untrimmed stats, so
    it can learn when a spike is signal and when it is noise.
    """
    if len(vals) <= 1:
        return _mean(vals)
    return _mean(sorted(vals)[:-1])


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
    """Compute rolling + contextual features for one market and upsert them.

    For every eligible-position player/game, builds features from a strictly
    prior lookback window (never the target game itself): base rolling stats
    (mean/stddev/weighted_mean/trend), market-specific engineered ratios
    (target share, yards per target/carry/attempt, opponent defensive rates),
    rolling snap share, nflverse's `ff_opportunity` expected-usage model, and
    the *current* game's pre-game Vegas/weather/injury context (not averaged
    over the window -- see the "Pre-game Vegas context" comment below for why
    that's legitimate, not leakage). Writes to `player_market_features`.

    See docs/ML_PIPELINE.md for the full feature list and rationale.
    """
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

    # NOTE: team_offense_pass, team_defense_rec, and team_defense_rec_rolling
    # below are not CTEs defined in this query -- they're real Postgres VIEWS
    # (see db/views/create_team_pass.sql, create_rec_defense_fixed.sql,
    # create_rolling_defense.sql). Easy to miss since every other join target
    # here is a local WITH-clause CTE.
    sql = f"""
        WITH pos_season_prior AS (
            -- Position-level mean from *earlier seasons only*, used as the
            -- shrinkage prior for players without much history. Restricting it
            -- to prior seasons keeps the prior independent of the season being
            -- predicted, which a pooled all-time mean would not be.
            SELECT position, season,
                   AVG(avg_stat) OVER (
                       PARTITION BY position ORDER BY season
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS prior_pos_mean
            FROM (
                SELECT position, season,
                       AVG(COALESCE({stat_field}, 0)::float8) AS avg_stat
                FROM player_game_stats_app
                WHERE position IS NOT NULL AND season IS NOT NULL
                GROUP BY position, season
            ) t
        ),
        opp_pos_defense AS (
            -- What a defense concedes *to a given position group*, not in total.
            -- A team can allow few yards overall while being soft against
            -- receiving backs, and a generic team total hides exactly the
            -- matchup a prop is priced on ("their run defense is terrible").
            SELECT
                opponent AS defense_team,
                game_date,
                position,
                SUM(COALESCE(rushing_yards, 0))::float8   AS pos_rush_yds_allowed,
                SUM(COALESCE(carries, 0))::float8         AS pos_carries_allowed,
                SUM(COALESCE(receiving_yards, 0))::float8 AS pos_rec_yds_allowed,
                SUM(COALESCE(receptions, 0))::float8      AS pos_recs_allowed,
                SUM(COALESCE(targets, 0))::float8         AS pos_targets_allowed,
                SUM(COALESCE(rushing_tds, 0) + COALESCE(receiving_tds, 0))::float8
                    AS pos_tds_allowed
            FROM player_game_stats_app
            WHERE game_date IS NOT NULL
              AND opponent IS NOT NULL
              AND position IS NOT NULL
            GROUP BY opponent, game_date, position
        ),
        opp_pos_form AS (
            -- A defense's recent form, as of each game date, against each
            -- position group. Averaged over its OWN previous games and shifted
            -- one row back so the game being predicted never contributes to it.
            SELECT
                defense_team,
                game_date,
                position,
                AVG(pos_rush_yds_allowed) OVER w AS form_pos_rush_yds,
                AVG(pos_carries_allowed)  OVER w AS form_pos_carries,
                AVG(pos_rec_yds_allowed)  OVER w AS form_pos_rec_yds,
                AVG(pos_recs_allowed)     OVER w AS form_pos_recs,
                AVG(pos_targets_allowed)  OVER w AS form_pos_targets,
                AVG(pos_tds_allowed)      OVER w AS form_pos_tds
            FROM opp_pos_defense
            WINDOW w AS (
                PARTITION BY defense_team, position
                ORDER BY game_date
                ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING
            )
        ),
        team_rush_offense AS (
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
        ),
        team_pass_defense_form AS (
            SELECT defense_team, game_date,
                   AVG(opp_pass_attempts_allowed) OVER w AS form_pass_att_allowed,
                   AVG(opp_pass_yards_allowed)    OVER w AS form_pass_yds_allowed
            FROM team_pass_defense
            WINDOW w AS (PARTITION BY defense_team ORDER BY game_date
                         ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING)
        ),
        team_rush_defense_form AS (
            SELECT defense_team, game_date,
                   AVG(opp_carries_allowed)    OVER w AS form_carries_allowed,
                   AVG(opp_rush_yards_allowed) OVER w AS form_rush_yards_allowed
            FROM team_rush_defense
            WINDOW w AS (PARTITION BY defense_team ORDER BY game_date
                         ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING)
        )
        SELECT
            pgs.player_id,
            pgs.team,
            pgs.position,
            pgs.game_date,
            pgs.opponent,
            pgs.season,

            COALESCE(pgs.{stat_field}, 0)::float8 AS y,
            psp.prior_pos_mean AS prior_pos_mean,
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
            COALESCE(trd.opp_carries_allowed, 0)::float8 AS opp_carries_allowed,

            COALESCE(sc.offense_pct, 0)::float8 AS snap_pct,
            COALESCE(fo.rec_yards_gained_exp, 0)::float8 AS exp_rec_yards,
            COALESCE(fo.receptions_exp, 0)::float8 AS exp_receptions,
            COALESCE(fo.rec_touchdown_exp, 0)::float8 AS exp_rec_td,
            COALESCE(fo.rush_yards_gained_exp, 0)::float8 AS exp_rush_yards,
            COALESCE(fo.rush_touchdown_exp, 0)::float8 AS exp_rush_td,
            COALESCE(fo.pass_yards_gained_exp, 0)::float8 AS exp_pass_yards,

            ng.home_team AS game_home_team,
            ng.away_team AS game_away_team,
            ng.spread_line AS game_spread_line,
            ng.total_line AS game_total_line,
            ng.temp AS game_temp,
            ng.wind AS game_wind,
            COALESCE(ng.div_game, 0)::float8 AS game_div_game,
            inj.report_status AS game_injury_status,
            -- Venue and situation, all known before kickoff.
            ng.roof            AS game_roof,
            ng.surface         AS game_surface,
            ng.home_rest       AS game_home_rest,
            ng.away_rest       AS game_away_rest,
            -- Play-context usage from pbp_player_game (previously unused).
            COALESCE(pbp.red_zone_targets, 0)::float8      AS rz_targets,
            COALESCE(pbp.red_zone_carries, 0)::float8      AS rz_carries,
            COALESCE(pbp.red_zone_target_rate, 0)::float8  AS rz_target_rate,
            COALESCE(pbp.third_down_targets, 0)::float8    AS third_down_targets,
            COALESCE(pbp.shotgun_pct, 0)::float8           AS shotgun_pct,
            COALESCE(pbp.avg_air_yards_target, 0)::float8  AS air_yards,
            COALESCE(pbp.avg_yac, 0)::float8               AS yac,
            COALESCE(pbp.avg_epa_per_play, 0)::float8      AS epa_per_play,
            COALESCE(pbp.avg_vegas_wp, 0)::float8          AS vegas_wp,
            COALESCE(pbp.total_plays, 0)::float8           AS player_plays,
            -- Opponent defense against this player's own position group.
            COALESCE(opd.pos_rush_yds_allowed, 0)::float8  AS opp_pos_rush_yds,
            COALESCE(opd.pos_carries_allowed, 0)::float8   AS opp_pos_carries,
            COALESCE(opd.pos_rec_yds_allowed, 0)::float8   AS opp_pos_rec_yds,
            COALESCE(opd.pos_recs_allowed, 0)::float8      AS opp_pos_recs,
            COALESCE(opd.pos_targets_allowed, 0)::float8   AS opp_pos_targets,
            COALESCE(opd.pos_tds_allowed, 0)::float8       AS opp_pos_tds,
            -- The upcoming opponent's own recent form (see opp_pos_form). Opf.form_pos_rush_yds  AS form_pos_rush_yds,
            opf.form_pos_carries   AS form_pos_carries,
            opf.form_pos_rec_yds   AS form_pos_rec_yds,
            opf.form_pos_recs      AS form_pos_recs,
            opf.form_pos_targets   AS form_pos_targets,
            opf.form_pos_tds       AS form_pos_tds,
            trdf.form_carries_allowed    AS form_carries_allowed,
            trdf.form_rush_yards_allowed AS form_rush_yards_allowed,
            tpdf.form_pass_att_allowed   AS form_pass_att_allowed,
            tpdf.form_pass_yds_allowed   AS form_pass_yds_allowed,
            dc.depth_rank AS depth_rank,
            COALESCE(tinj.pos_teammates_out, 0)::float8 AS pos_teammates_out,
            COALESCE(tinj.pos_teammates_questionable, 0)::float8 AS pos_teammates_questionable

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
        LEFT JOIN snap_counts sc
            ON sc.player_id = pgs.player_id
           AND sc.game_id = pgs.game_id
        LEFT JOIN ff_opportunity fo
            ON fo.player_id = pgs.player_id
           AND fo.game_id = pgs.game_id
        LEFT JOIN nfl_games ng
            ON ng.game_id = pgs.game_id
        LEFT JOIN pos_season_prior psp
            ON psp.position = pgs.position
           AND psp.season = pgs.season
        LEFT JOIN opp_pos_form opf
            ON opf.defense_team = pgs.opponent
           AND opf.game_date = pgs.game_date
           AND opf.position = pgs.position
        LEFT JOIN team_rush_defense_form trdf
            ON trdf.defense_team = pgs.opponent
           AND trdf.game_date = pgs.game_date
        LEFT JOIN team_pass_defense_form tpdf
            ON tpdf.defense_team = pgs.opponent
           AND tpdf.game_date = pgs.game_date
        LEFT JOIN pbp_player_game pbp
            ON pbp.player_id = pgs.player_id
           AND pbp.game_id = pgs.game_id
        LEFT JOIN opp_pos_defense opd
            ON opd.defense_team = pgs.opponent
           AND opd.game_date = pgs.game_date
           AND opd.position = pgs.position
        LEFT JOIN injuries inj
            ON inj.player_id = pgs.player_id
           AND inj.season = pgs.season
           AND inj.week = pgs.week
        -- Depth chart rank for the game being predicted. A player's listed
        -- starter/backup rank is known before kickoff and captures role changes
        -- (promotion to WR1, RB committee shakeup) faster than rolling production.
        LEFT JOIN (
            SELECT player_id, season, week, MIN(depth_team) AS depth_rank
            FROM depth_charts
            WHERE depth_team IS NOT NULL
            GROUP BY player_id, season, week
        ) dc
            ON dc.player_id = pgs.player_id
           AND dc.season = pgs.season
           AND dc.week = pgs.week
        -- Teammates at the same position listed on this week's injury report.
        -- When the man ahead of you is out, your opportunity spikes in a way no
        -- rolling average of your own past production can anticipate.
        LEFT JOIN (
            SELECT team, season, week, position,
                   COUNT(*) FILTER (WHERE report_status IN ('Out', 'Doubtful'))
                       AS pos_teammates_out,
                   COUNT(*) FILTER (WHERE report_status = 'Questionable')
                       AS pos_teammates_questionable
            FROM injuries
            GROUP BY team, season, week, position
        ) tinj
            ON tinj.team = pgs.team
           AND tinj.season = pgs.season
           AND tinj.week = pgs.week
           AND tinj.position = pgs.position
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
            # The game being predicted. Defined up front because several feature
            # families read the opponent's own context from it, not just the
            # Vegas block further down.
            target_game = games[i]

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

            # Outlier-resistant views of the same lookback window. `mean` and
            # `weighted_mean` are both dominated by a single big game at
            # lookback=5, which is the main source of the model overshooting a
            # line after one spike. Offering a robust centre (median, trimmed
            # mean) plus the raw spread lets the model decide how much of a hot
            # window to believe rather than baking in a fixed shrinkage rule.
            # Panel time-series view of the player's level.
            #
            # `weighted_mean` is a fixed 5-game window with linear weights --
            # both the length and the weights were guessed. A local-level model
            # says the true level drifts and each game observes it with noise,
            # which makes the decay a parameter to estimate. Fitted on held-out
            # folds (`eval_timeseries.py`), the best decay is alpha ~= 0.25, a
            # half-life of about 2.4 games -- markedly faster than the shipped
            # window implies. On the same folds this lifts R2 on the level
            # estimate alone: rec_yds 0.327 -> 0.381, recs 0.387 -> 0.433.
            #
            # It also uses the player's whole history rather than truncating at
            # five games, so a long track record is not thrown away.
            prior = ys[:i]
            if prior:
                alpha = 0.35 if market_code == "rush_att" else 0.25
                level = prior[0]
                for v in prior[1:]:
                    level = alpha * v + (1 - alpha) * level
                extra_features["ewma_level"] = level

                # Empirical-Bayes shrinkage toward the position's prior-season
                # mean, weighted by how much of the player we have actually
                # seen. Three games of history get pulled hard toward the
                # position; sixty games barely move. This is the standard answer
                # to a panel of many short, noisy series.
                pos_prior = target_game.get("prior_pos_mean")
                if pos_prior is not None:
                    n_seen = float(len(prior))
                    w = n_seen / (n_seen + 2.0)
                    extra_features["ewma_shrunk"] = (
                        w * level + (1.0 - w) * float(pos_prior)
                    )
                    extra_features["career_n"] = n_seen

            if window:
                extra_features["y_median"] = _median(window)
                extra_features["y_trimmed_mean"] = _trimmed_mean(window)
                extra_features["y_max"] = max(window)
                extra_features["y_min"] = min(window)

            # Season-to-date baseline over every prior game this season, not
            # just the last `lookback`. A slower anchor to revert toward, with
            # its own sample size so the model can learn how far to trust it.
            season_prior = [
                float(g["y"] or 0.0)
                for g in games[:i]
                if g.get("season") == games[i].get("season")
            ]
            if season_prior:
                extra_features["y_season_mean"] = _mean(season_prior)
                extra_features["y_season_n"] = float(len(season_prior))

            for code, _col in upstream_cols:
                vals = [float(g.get(code, 0.0) or 0.0) for g in window_games]
                if vals:
                    extra_features[f"{code}_mean"] = _mean(vals)
                    extra_features[f"{code}_trend"] = _trend_slope(vals)

            # Snap share (offense_pct from nflverse snap_counts) is a strong,
            # market-agnostic proxy for opportunity/role that isn't captured
            # by rolling production stats alone (e.g. a role change shows up
            # in snaps before it shows up in a full box score sample).
            snap_pct_window = [
                float(g.get("snap_pct", 0.0) or 0.0) for g in window_games
            ]
            snap_pct_nonzero = [v for v in snap_pct_window if v > 0]
            if snap_pct_nonzero:
                extra_features["snap_share_mean"] = _mean(snap_pct_nonzero)
                extra_features["snap_share_trend"] = _trend_slope(snap_pct_nonzero)

            # nflverse's own play-context "expected" opportunity model
            # (ff_opportunity), lagged the same way as every other feature.
            # This carries signal the raw box score mean/weighted_mean can't:
            # it reflects usage implied by air yards / red zone role / etc.,
            # so it can disagree with a player's recent box score (e.g. after
            # a drop-heavy or garbage-time-heavy game) in informative ways.
            if feature_family == "receiving":
                exp_rec_yards_window = [
                    float(g.get("exp_rec_yards", 0.0) or 0.0) for g in window_games
                ]
                exp_receptions_window = [
                    float(g.get("exp_receptions", 0.0) or 0.0) for g in window_games
                ]
                if any(exp_rec_yards_window):
                    extra_features["exp_rec_yards_mean"] = _mean(exp_rec_yards_window)
                    extra_features["exp_rec_yards_trend"] = _trend_slope(exp_rec_yards_window)
                if any(exp_receptions_window):
                    extra_features["exp_receptions_mean"] = _mean(exp_receptions_window)
                    extra_features["exp_receptions_trend"] = _trend_slope(exp_receptions_window)

            elif feature_family == "rushing":
                exp_rush_yards_window = [
                    float(g.get("exp_rush_yards", 0.0) or 0.0) for g in window_games
                ]
                if any(exp_rush_yards_window):
                    extra_features["exp_rush_yards_mean"] = _mean(exp_rush_yards_window)
                    extra_features["exp_rush_yards_trend"] = _trend_slope(exp_rush_yards_window)

            elif feature_family == "passing":
                exp_pass_yards_window = [
                    float(g.get("exp_pass_yards", 0.0) or 0.0) for g in window_games
                ]
                if any(exp_pass_yards_window):
                    extra_features["exp_pass_yards_mean"] = _mean(exp_pass_yards_window)
                    extra_features["exp_pass_yards_trend"] = _trend_slope(exp_pass_yards_window)

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
                # Defensive form of the opponent in THIS game (see opp_pos_form).
                # Previously averaged over the lookback window, which described
                # the defenses the player had just faced rather than the one he
                # was about to face.
                v = target_game.get("form_rush_yards_allowed")
                if v is not None:
                    extra_features["opp_rush_yards_allowed"] = float(v)
                c = target_game.get("form_carries_allowed")
                if c is not None:
                    extra_features["opp_carries_allowed"] = float(c)
                if v is not None and c is not None and float(c) > 0:
                    extra_features["opp_yards_per_carry_allowed"] = float(v) / float(c)

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

                # Passing defense of the opponent in THIS game, not an average
                # over the defenses already played (see opp_pos_form).
                pa = target_game.get("form_pass_att_allowed")
                py = target_game.get("form_pass_yds_allowed")
                if pa is not None:
                    extra_features["opp_pass_attempts_allowed"] = float(pa)
                if py is not None:
                    extra_features["opp_pass_yards_allowed"] = float(py)
                if pa is not None and py is not None and float(pa) > 0:
                    extra_features["opp_yards_per_attempt_allowed"] = float(py) / float(pa)

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

                # Receiving defense of the opponent in THIS game, normalised by
                # the pass volume they face so a team is not flattered simply for
                # playing run-heavy opponents. Previously averaged over the
                # window, which described the wrong defenses entirely.
                opp_rec_form = target_game.get("form_pos_rec_yds")
                opp_tgt_form = target_game.get("form_pos_targets")
                team_pass_ref = _mean(team_pass_window) if team_pass_window else 0.0
                if opp_rec_form is not None and team_pass_ref > 0:
                    extra_features["opp_rec_yds_per_attempt"] = (
                        float(opp_rec_form) / team_pass_ref
                    )
                if opp_tgt_form is not None and team_pass_ref > 0:
                    extra_features["opp_target_rate_allowed"] = (
                        float(opp_tgt_form) / team_pass_ref
                    )

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

            # Pre-game Vegas context for the game being predicted (games[i]
            # itself, not the lookback window). This is known before kickoff
            # -- same information a sportsbook prop line is priced off of --
            # so it's not leakage, and it carries far more signal about this
            # specific game's likely script/volume than any rolling average
            # of past games can.
            spread_line = target_game.get("game_spread_line")
            total_line = target_game.get("game_total_line")
            home_team = target_game.get("game_home_team")
            away_team = target_game.get("game_away_team")
            if spread_line is not None and total_line is not None and home_team:
                spread_line = float(spread_line)
                total_line = float(total_line)
                if target_game.get("team") == home_team:
                    team_implied_total = (total_line + spread_line) / 2.0
                    team_spread = spread_line
                elif target_game.get("team") == away_team:
                    team_implied_total = (total_line - spread_line) / 2.0
                    team_spread = -spread_line
                else:
                    team_implied_total = total_line / 2.0
                    team_spread = 0.0
                extra_features["game_total_line"] = total_line
                extra_features["team_implied_total"] = team_implied_total
                extra_features["team_spread"] = team_spread

            game_wind = target_game.get("game_wind")
            game_temp = target_game.get("game_temp")
            if game_wind is not None:
                extra_features["game_wind"] = float(game_wind)
            if game_temp is not None:
                extra_features["game_temp"] = float(game_temp)
            extra_features["game_div_game"] = float(target_game.get("game_div_game", 0.0) or 0.0)

            # Venue and situation for the game being predicted. Indoor removes
            # weather entirely, surface affects pace, and rest days separate a
            # short-week Thursday game from a bye-week return -- all known well
            # before kickoff and none of it previously used.
            roof = (target_game.get("game_roof") or "").strip().lower()
            if roof:
                # "closed" is a retractable roof shut for the game, so it plays
                # as a dome; "open" is a retractable roof left open.
                extra_features["is_indoor"] = 1.0 if roof in ("dome", "closed") else 0.0
            surface = (target_game.get("game_surface") or "").strip().lower()
            if surface:
                extra_features["is_turf"] = 0.0 if "grass" in surface else 1.0

            is_home = 1.0 if target_game.get("team") == home_team else 0.0
            extra_features["is_home"] = is_home
            rest = target_game.get("game_home_rest" if is_home else "game_away_rest")
            if rest is not None:
                extra_features["rest_days"] = float(rest)

            # Rolling play-context usage. Red-zone volume is the single most
            # direct predictor of touchdowns, which plain box-score rates miss
            # entirely: a back with 4 red-zone carries a game is a different
            # proposition from one with the same yardage and none.
            for col in (
                "rz_targets", "rz_carries", "rz_target_rate", "third_down_targets",
                "shotgun_pct", "air_yards", "yac", "epa_per_play", "vegas_wp",
                "player_plays",
            ):
                vals = [float(g.get(col, 0.0) or 0.0) for g in window_games]
                if any(vals):
                    extra_features[f"{col}_mean"] = _mean(vals)
                    extra_features[f"{col}_trend"] = _trend_slope(vals)

            # How the opponent *being faced in this game* has defended this
            # player's position group lately.
            #
            # This must come from the target game's own row, not from an average
            # over the lookback window: the window's opponents are the teams the
            # player just played, which have nothing to do with the one he is
            # about to play. Averaging them measured strength of schedule while
            # being named, and used, as a matchup signal.
            for src, dest in (
                ("form_pos_rush_yds", "opp_pos_rush_yds_allowed"),
                ("form_pos_carries", "opp_pos_carries_allowed"),
                ("form_pos_rec_yds", "opp_pos_rec_yds_allowed"),
                ("form_pos_recs", "opp_pos_recs_allowed"),
                ("form_pos_targets", "opp_pos_targets_allowed"),
                ("form_pos_tds", "opp_pos_tds_allowed"),
            ):
                v = target_game.get(src)
                if v is not None:
                    extra_features[dest] = float(v)

            # Player's own current-week injury report (pre-game known). Rows
            # with no injury report entry are healthy by construction (only
            # injured players get listed), so absence -> all flags 0.
            injury_status = (target_game.get("game_injury_status") or "").strip()
            extra_features["injury_questionable"] = 1.0 if injury_status == "Questionable" else 0.0
            extra_features["injury_doubtful"] = 1.0 if injury_status == "Doubtful" else 0.0
            extra_features["injury_out"] = 1.0 if injury_status == "Out" else 0.0

            # Depth chart rank for this game (1 = starter). Left unset when the
            # player is not on that week's depth chart so the model sees a
            # missing value rather than a fake rank.
            depth_rank = target_game.get("depth_rank")
            if depth_rank is not None:
                extra_features["depth_rank"] = float(depth_rank)

            # How the player's role has *changed* relative to the games the
            # rolling stats were built from.
            #
            # This matters because depth rank on its own is nearly worthless to
            # the model (importance 0.002 for rush_yds): in training, rank and
            # production always agree, so the trees just split on carries and
            # ignore the rank. The one case where they disagree is exactly the
            # case that matters -- a back whose window says workhorse but who has
            # since been moved down the chart behind a new signing. Encoding the
            # delta gives the model something the production features cannot say.
            depth_window = [
                float(g["depth_rank"])
                for g in window_games
                if g.get("depth_rank") is not None
            ]
            if depth_window:
                window_rank = _mean(depth_window)
                extra_features["depth_rank_window_mean"] = window_rank
                if depth_rank is not None:
                    # positive = demoted since the window, negative = promoted
                    extra_features["depth_rank_delta"] = float(depth_rank) - window_rank

            # How stale the window is. A Week 1 projection is built from games
            # played the previous January; the model should be able to discount
            # a window that is months rather than days old.
            prev_date = window_games[-1].get("game_date") if window_games else None
            cur_date = target_game.get("game_date")
            if prev_date is not None and cur_date is not None:
                extra_features["days_since_last_game"] = float(
                    (cur_date - prev_date).days
                )

                # Role change and window staleness interact, and the interaction
                # is far stronger than either term alone. Measured on the
                # training rows (rush_yds, window mean > 10), actual output as a
                # fraction of the rolling weighted mean:
                #
                #   demoted,  in-season window     0.88   (n=108)
                #   demoted,  window > 60 days     0.44   (n=46)
                #   promoted, window > 60 days     1.50   (n=24)
                #   same rank, in-season           1.01   (n=3811)
                #
                # A back who was the workhorse in January and is second on the
                # chart in September produces well under half his old numbers.
                # That bucket is 0.3% of rows, so a tree ensemble will not
                # discover the three-way interaction on its own -- handing it the
                # term directly costs nothing and gives it a single split. The
                # magnitude is still learned from data, not asserted here.
                stale_days = extra_features["days_since_last_game"]
                delta = extra_features.get("depth_rank_delta")
                extra_features["is_stale_window"] = 1.0 if stale_days > 60 else 0.0
                if delta is not None:
                    extra_features["stale_role_change"] = (
                        float(delta) if stale_days > 60 else 0.0
                    )

            # Teammates at the same position on the injury report this week.
            # The player's own listing is subtracted so this counts only the
            # competition for touches that is banged up, not the player.
            teammates_out = float(target_game.get("pos_teammates_out", 0.0) or 0.0)
            teammates_q = float(target_game.get("pos_teammates_questionable", 0.0) or 0.0)
            if injury_status in ("Out", "Doubtful"):
                teammates_out = max(0.0, teammates_out - 1.0)
            elif injury_status == "Questionable":
                teammates_q = max(0.0, teammates_q - 1.0)
            extra_features["pos_teammates_out"] = teammates_out
            extra_features["pos_teammates_questionable"] = teammates_q

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
    """Fill in `label_actual` for rows whose target game has since been played.

    Matches `player_market_features` rows to `player_game_stats_app` on
    (player_id, as_of_game_date, opponent, market_id) and copies the real
    stat value. Run this after a game week completes and before retraining.
    """
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
