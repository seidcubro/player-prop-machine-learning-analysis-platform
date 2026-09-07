"""Compute betting edges: sportsbook props vs. model projections.

For every sportsbook player prop in odds_player_props, this:
1. maps the odds market key to an internal market_code (ODDS_TO_MARKET)
2. finds the single most-recent player_market_features row as of (<=) the
   event date -- deliberately NOT by searching history for a prior game
   against the same opponent (that was the root cause of a long-standing
   overprojection bug: it could pick up a rolling-window snapshot from a
   different season/team whenever an opponent repeated -- see
   docs/ML_PIPELINE.md "History"). Opponent-specific signal comes from that
   row's own opp_* extra_features, not from re-selecting an old row.
3. predicts with the market's active model, un-transforms if target_transform
   calls for it, blends with the rolling weighted_mean, and clamps to within
   one stddev of weighted_mean
4. compares to the sportsbook line to get win probability (normal
   approximation) and an edge tier
5. writes everything to prop_edges

Not currently exposed via any API route -- see docs/API.md "The gap".
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import create_engine, text

import math

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "app")
POSTGRES_USER = os.getenv("POSTGRES_USER", "app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "app")
ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")

DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

ODDS_TO_MARKET = {
    "player_pass_attempts": "pass_att",
    "player_pass_yds": "pass_yds",
    "player_pass_tds": "pass_td",
    "player_pass_completions": "pass_completions",
    "player_rush_attempts": "rush_att",
    "player_rush_yds": "rush_yds",
    "player_rush_tds": "rush_td",
    "player_receptions": "recs",
    "player_reception_yds": "rec_yds",
    "player_reception_tds": "rec_td",
}

TEAM_MAP = {
    "ari": "arizona cardinals",
    "atl": "atlanta falcons",
    "bal": "baltimore ravens",
    "buf": "buffalo bills",
    "car": "carolina panthers",
    "chi": "chicago bears",
    "cin": "cincinnati bengals",
    "cle": "cleveland browns",
    "dal": "dallas cowboys",
    "den": "denver broncos",
    "det": "detroit lions",
    "gb": "green bay packers",
    "hou": "houston texans",
    "ind": "indianapolis colts",
    "jax": "jacksonville jaguars",
    "kc": "kansas city chiefs",
    "lv": "las vegas raiders",
    "lac": "los angeles chargers",
    "lar": "los angeles rams",
    # nflverse writes the Rams as "LA" and Washington as "WAS"; the LAR/WSH
    # spellings never matched a row, so those players silently fell out.
    "la": "los angeles rams",
    "was": "washington commanders",
    "mia": "miami dolphins",
    "min": "minnesota vikings",
    "ne": "new england patriots",
    "no": "new orleans saints",
    "nyg": "new york giants",
    "nyj": "new york jets",
    "phi": "philadelphia eagles",
    "pit": "pittsburgh steelers",
    "sf": "san francisco 49ers",
    "sea": "seattle seahawks",
    "tb": "tampa bay buccaneers",
    "ten": "tennessee titans",
    "wsh": "washington commanders",
}

def normalize_name(name: str) -> str:
    return " ".join((name or "").lower().replace(".", "").replace("-", " ").split())

def normalize_team(name: str) -> str:
    return " ".join((name or "").lower().replace(".", "").replace("-", " ").split())


def edge_tier(raw_edge: float) -> str:
    a = abs(raw_edge)
    if a >= 15:
        return "elite"
    if a >= 10:
        return "strong"
    if a >= 5:
        return "medium"
    if a >= 2:
        return "small"
    return "none"


def load_current_context(engine) -> dict:
    """Look up the state of the world for the games being predicted.

    Every "known before kickoff" feature -- depth chart rank, injury status,
    Vegas line, weather, venue -- was previously inherited from the player's most
    recent *historical* feature row. In-season that row is a week old and the
    error is small. In September it is eight months old, and the projection is
    built on last season's depth chart: Woody Marks was Houston's RB1 in January
    2026 and is RB2 behind David Montgomery on the 2026 chart, but the model
    could not see that.

    Returns the latest depth chart rank and injury status per player, plus the
    scheduled games keyed by (team, date) so the upcoming game's own venue and
    Vegas context can be substituted in.
    """
    depth = pd.read_sql(
        text(
            """
            -- A player can appear at several depth positions in the same week
            -- (different formation slots), so take his best rank -- matching the
            -- MIN(depth_team) grouping build_features uses. Picking an arbitrary
            -- tied row would make inference disagree with training.
            SELECT DISTINCT ON (player_id)
                   player_id, depth_team, season, week
            FROM (
                SELECT player_id, season, week, MIN(depth_team) AS depth_team
                FROM depth_charts
                WHERE depth_team IS NOT NULL
                GROUP BY player_id, season, week
            ) d
            ORDER BY player_id, season DESC, week DESC
            """
        ),
        engine,
    ).set_index("player_id")

    inj = pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (player_id) player_id, report_status, season, week
            FROM injuries
            ORDER BY player_id, season DESC, week DESC
            """
        ),
        engine,
    ).set_index("player_id")

    games = pd.read_sql(
        text(
            """
            SELECT game_id, game_date, home_team, away_team, spread_line,
                   total_line, roof, surface, temp, wind, div_game,
                   home_rest, away_rest
            FROM nfl_games
            WHERE game_date IS NOT NULL
            """
        ),
        engine,
    )
    games["game_date"] = pd.to_datetime(games["game_date"]).dt.date

    by_team_date = {}
    for g in games.itertuples(index=False):
        by_team_date[(g.home_team, g.game_date)] = (g, True)
        by_team_date[(g.away_team, g.game_date)] = (g, False)

    # Each defense's most recent form against each position group, so the
    # matchup features describe the team actually being faced.
    opp_form = pd.read_sql(
        text(
            """
            WITH per_game AS (
                SELECT opponent AS defense_team, game_date, position,
                       SUM(COALESCE(rushing_yards, 0))::float8   AS rush_yds,
                       SUM(COALESCE(carries, 0))::float8         AS carries,
                       SUM(COALESCE(receiving_yards, 0))::float8 AS rec_yds,
                       SUM(COALESCE(receptions, 0))::float8      AS recs,
                       SUM(COALESCE(targets, 0))::float8         AS targets,
                       SUM(COALESCE(rushing_tds, 0)
                           + COALESCE(receiving_tds, 0))::float8 AS tds
                FROM player_game_stats_app
                WHERE opponent IS NOT NULL AND position IS NOT NULL
                  AND game_date IS NOT NULL
                GROUP BY opponent, game_date, position
            ),
            ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY defense_team, position ORDER BY game_date DESC
                ) AS rn
                FROM per_game
            )
            SELECT defense_team, position,
                   AVG(rush_yds) AS form_pos_rush_yds,
                   AVG(carries)  AS form_pos_carries,
                   AVG(rec_yds)  AS form_pos_rec_yds,
                   AVG(recs)     AS form_pos_recs,
                   AVG(targets)  AS form_pos_targets,
                   AVG(tds)      AS form_pos_tds
            FROM ranked WHERE rn <= 8
            GROUP BY defense_team, position
            """
        ),
        engine,
    ).set_index(["defense_team", "position"])

    # Team-level defensive form, for the markets that use team totals.
    team_form = pd.read_sql(
        text(
            """
            WITH per_game AS (
                SELECT opponent AS defense_team, game_date,
                       SUM(COALESCE(carries, 0))::float8       AS carries,
                       SUM(COALESCE(rushing_yards, 0))::float8 AS rush_yds,
                       SUM(COALESCE(attempts, 0))::float8      AS pass_att,
                       SUM(COALESCE(passing_yards, 0))::float8 AS pass_yds
                FROM player_game_stats_app
                WHERE opponent IS NOT NULL AND game_date IS NOT NULL
                GROUP BY opponent, game_date
            ),
            ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY defense_team ORDER BY game_date DESC) AS rn
                FROM per_game
            )
            SELECT defense_team,
                   AVG(carries)  AS form_carries_allowed,
                   AVG(rush_yds) AS form_rush_yards_allowed,
                   AVG(pass_att) AS form_pass_att_allowed,
                   AVG(pass_yds) AS form_pass_yds_allowed
            FROM ranked WHERE rn <= 8
            GROUP BY defense_team
            """
        ),
        engine,
    ).set_index("defense_team")

    # Teammates on this week's injury report, by team/position.
    mates = pd.read_sql(
        text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (player_id) player_id, team, position,
                       report_status, season, week
                FROM injuries
                ORDER BY player_id, season DESC, week DESC
            )
            SELECT team, position,
                   COUNT(*) FILTER (WHERE report_status IN ('Out', 'Doubtful'))
                       AS teammates_out,
                   COUNT(*) FILTER (WHERE report_status = 'Questionable')
                       AS teammates_questionable
            FROM latest
            WHERE team IS NOT NULL AND position IS NOT NULL
            GROUP BY team, position
            """
        ),
        engine,
    ).set_index(["team", "position"])

    print(
        f"  current context: {len(depth)} depth ranks, {len(inj)} injury rows, "
        f"{len(games)} scheduled games, {len(opp_form)} defense/position form rows"
    )
    return {
        "depth": depth,
        "injuries": inj,
        "games": by_team_date,
        "opp_form": opp_form,
        "team_form": team_form,
        "mates": mates,
    }


def apply_current_context(
    row_features: dict, ctx: dict, player_id: str, team: str, event_date,
    position: str | None = None,
) -> None:
    """Overwrite pre-kickoff-known features with the upcoming game's own values.

    Only touches keys the model already has; anything absent from this market's
    feature set is left alone. Rolling *history* features are untouched by
    design -- those legitimately describe the past.
    """

    def put(key, value):
        """Set a feature only if the model has it and the value is usable.

        Missing data here is normal rather than exceptional: weather is unknown
        until close to kickoff and a line may not be posted yet. pandas returns
        NaN rather than None for those, and NaN reaches the estimator as a hard
        error, so the existing value is left in place instead.
        """
        if key not in row_features or value is None:
            return
        try:
            f = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(f):
            row_features[key] = f

    depth = ctx["depth"]
    if player_id in depth.index:
        current_rank = depth.at[player_id, "depth_team"]
        put("depth_rank", current_rank)
        # Recompute the delta against the window the rolling stats came from.
        # Substituting only the current rank would leave a delta computed from
        # last season's rank, which is the stale value this override exists to
        # replace: Woody Marks is RB1 in the window and RB2 now, so the model
        # must see +1 (demoted), not the 0 the historical row carries.
        window_rank = row_features.get("depth_rank_window_mean")
        if window_rank is not None and current_rank is not None:
            try:
                put("depth_rank_delta", float(current_rank) - float(window_rank))
            except (TypeError, ValueError):
                pass

    inj = ctx["injuries"]
    status = ""
    if player_id in inj.index:
        status = (inj.at[player_id, "report_status"] or "").strip()
    # Absence of a listing means healthy: only injured players get reported.
    put("injury_questionable", 1.0 if status == "Questionable" else 0.0)
    put("injury_doubtful", 1.0 if status == "Doubtful" else 0.0)
    put("injury_out", 1.0 if status == "Out" else 0.0)

    # Staleness of the rolling window, measured to the game actually being
    # predicted rather than to whatever game the historical row described.
    last_played = row_features.get("_last_game_date")
    if last_played is not None:
        stale_days = (event_date - last_played).days
        put("days_since_last_game", stale_days)
        # These are derived from the two values above, so they have to be
        # recomputed rather than inherited from the stored row.
        put("is_stale_window", 1.0 if stale_days > 60 else 0.0)
        put(
            "stale_role_change",
            row_features.get("depth_rank_delta", 0.0) if stale_days > 60 else 0.0,
        )

    # Teammates on this week's injury report at the same position, minus the
    # player himself. Vacated opportunity is a now fact, not a window average.
    mates = ctx["mates"]
    if position is not None and (team, position) in mates.index:
        m = mates.loc[(team, position)]
        out_n = float(m["teammates_out"])
        q_n = float(m["teammates_questionable"])
        if status in ("Out", "Doubtful"):
            out_n = max(0.0, out_n - 1.0)
        elif status == "Questionable":
            q_n = max(0.0, q_n - 1.0)
        put("pos_teammates_out", out_n)
        put("pos_teammates_questionable", q_n)

    found = ctx["games"].get((team, event_date))
    if not found:
        return
    game, is_home = found

    # The defense actually being faced, replacing whatever opponent the stored
    # row happened to describe.
    opponent = game.away_team if is_home else game.home_team
    of = ctx["opp_form"]
    if position is not None and (opponent, position) in of.index:
        f = of.loc[(opponent, position)]
        for src, dest in (
            ("form_pos_rush_yds", "opp_pos_rush_yds_allowed"),
            ("form_pos_carries", "opp_pos_carries_allowed"),
            ("form_pos_rec_yds", "opp_pos_rec_yds_allowed"),
            ("form_pos_recs", "opp_pos_recs_allowed"),
            ("form_pos_targets", "opp_pos_targets_allowed"),
            ("form_pos_tds", "opp_pos_tds_allowed"),
        ):
            put(dest, f[src])

    tf = ctx["team_form"]
    if opponent in tf.index:
        t = tf.loc[opponent]
        put("opp_carries_allowed", t["form_carries_allowed"])
        # Receiving rate stats, normalised by the pass volume the defense faces
        # so a team is not flattered for playing run-heavy opponents.
        if position is not None and (opponent, position) in of.index:
            f2 = of.loc[(opponent, position)]
            pass_ref = t["form_pass_att_allowed"]
            if pass_ref and pass_ref > 0:
                put("opp_rec_yds_per_attempt", f2["form_pos_rec_yds"] / pass_ref)
                put("opp_target_rate_allowed", f2["form_pos_targets"] / pass_ref)
        put("opp_rush_yards_allowed", t["form_rush_yards_allowed"])
        put("opp_pass_attempts_allowed", t["form_pass_att_allowed"])
        put("opp_pass_yards_allowed", t["form_pass_yds_allowed"])
        if t["form_carries_allowed"] and t["form_carries_allowed"] > 0:
            put("opp_yards_per_carry_allowed",
                t["form_rush_yards_allowed"] / t["form_carries_allowed"])
        if t["form_pass_att_allowed"] and t["form_pass_att_allowed"] > 0:
            put("opp_yards_per_attempt_allowed",
                t["form_pass_yds_allowed"] / t["form_pass_att_allowed"])

    put("is_home", 1.0 if is_home else 0.0)
    put("game_div_game", game.div_game or 0.0)
    put("rest_days", game.home_rest if is_home else game.away_rest)

    roof = (game.roof or "").strip().lower()
    if roof:
        put("is_indoor", 1.0 if roof in ("dome", "closed") else 0.0)
    surface = (game.surface or "").strip().lower()
    if surface:
        put("is_turf", 0.0 if "grass" in surface else 1.0)

    # Weather is only published close to kickoff. Inheriting the stale row's
    # value would mean projecting a September game on January's weather, so an
    # unknown forecast is encoded as 0.0 -- exactly how training represents it,
    # since feature rows without weather simply omit the key and reach the model
    # as zero. Consistency with training matters more than a plausible guess.
    # pd.isna covers both None and NaN; an unset numeric column arrives as NaN,
    # which is not None and would otherwise slip through as "known".
    put("game_temp", 0.0 if pd.isna(game.temp) else game.temp)
    put("game_wind", 0.0 if pd.isna(game.wind) else game.wind)

    if game.spread_line is not None and game.total_line is not None:
        total = float(game.total_line)
        spread = float(game.spread_line)
        team_spread = spread if is_home else -spread
        put("game_total_line", total)
        put("team_spread", team_spread)
        put("team_implied_total", (total + team_spread) / 2.0)


def load_stale_role_factors(artifact_dir: Path) -> dict:
    """Load the measured correction for players returning in a reduced role.

    A player whose rolling window is months old and who has since dropped down
    the depth chart produces far less than that window implies. The model cannot
    learn this on its own: the case is ~0.3% of training rows, and with
    `max_features="sqrt"` the relevant feature is almost never even offered at a
    split (measured importance 0.0002 for rush_yds).

    The factors in this file are therefore estimated by `eval_stale_role.py`,
    shrunk toward 1.0 by sample size, and written **only** for markets where the
    correction reduced error on a held-out period it was not fitted on. A market
    with no entry is left untouched.
    """
    path = artifact_dir / "stale_role_factors.json"
    if not path.exists():
        return {}
    factors = json.loads(path.read_text(encoding="utf-8"))
    if factors:
        summary = ", ".join(
            f"{k} x{v['factor']}" for k, v in sorted(factors.items())
        )
        print(f"  stale-role factors: {summary}")
    return factors


def load_probability_calibrator(artifact_dir: Path):
    """The final isotonic correction, fitted on graded results.

    The quantile bundles already carry their own calibration, and it is not
    enough: measured across 6,301 graded picks the site still claimed 65.6% and
    hit 51.5%. That fourteen-point gap is not merely a wrong number on screen,
    because the EV filter subtracts the break-even from this probability. An
    inflated P inflates EV by the same amount, so "EV > 10%" was really
    selecting bets at roughly -4% EV -- a losing filter built out of a model
    that does rank correctly (AUC 0.547).

    Fitted out of sample on 2023-24 and applied to 2025, this takes the mean
    probability from 0.654 to 0.503 against an actual 0.516, and Brier from
    0.2706 to 0.2511.
    """
    path = artifact_dir / "probability_calibrator.joblib"
    if not path.exists():
        print("  no probability calibrator found; probabilities ship uncorrected")
        return None
    bundle = joblib.load(path)
    print(f"  probability calibrator from {path.name} "
          f"({bundle.get('fitted_on', 0)} graded picks)")
    return bundle


def calibrate_probability(bundle, market_code: str, p: float) -> float:
    """Apply the market's calibrator, falling back to the pooled one.

    A market with too little graded history has no curve of its own, and the
    pooled correction is far closer to right than no correction at all.
    """
    if not bundle:
        return p
    models = bundle.get("models") or {}
    ir = models.get(market_code) or models.get("__pooled__")
    if ir is None:
        return p
    out = float(ir.predict(np.asarray([p], dtype=float))[0])
    return float(min(max(out, 0.01), 0.99))


def implied_prob(price) -> float:
    """Break-even win rate implied by an American price, vig included.

    This is the number a probability has to beat before a bet is worth making.
    Comparing against 0.5 instead treats a -130 line and a +130 line as the same
    proposition, which they are emphatically not.
    """
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return float("nan")
    try:
        p = float(price)
    except (TypeError, ValueError):
        return float("nan")
    if p == 0:
        return float("nan")
    return (-p) / ((-p) + 100.0) if p < 0 else 100.0 / (p + 100.0)


def prob_over_from_quantiles(qpreds: dict, line: float, calibration=None) -> float:
    """P(outcome > line), interpolated from predicted quantiles.

    The fitted quantiles are CDF points, so interpolating the level at `line`
    gives P(outcome <= line) directly. Values are sorted first because
    separately-fitted quantile models can cross, which would make the CDF
    non-monotonic. Clipped away from 0/1 -- no prop is ever a certainty, and a
    sportsbook line sitting outside the whole predicted range still should not
    read as 100%.

    `calibration` is the probability-integral-transform map that
    `train_quantiles.py` fits on the training rows. Without it the raw CDF is
    biased the same way in every market: the low quantiles come out too high, so
    too much mass sits below the line and P(over) reads low. Measured on the
    2025 holdout the raw q10 covered 25-31% of outcomes instead of 10%. Applying
    the map is what moved the held-out EV strategy from break-even to a 95%
    interval clear of zero, so it is not optional when one is present.
    """
    levels = sorted(qpreds.keys())
    values = sorted(qpreds[q] for q in levels)
    p_under = float(np.interp(line, values, levels))
    if calibration:
        p_under = float(np.interp(p_under, calibration["grid"],
                                  calibration["level"]))
    return float(min(max(1.0 - p_under, 0.02), 0.98))


# Markets where the outcome is a small integer.
#
# Quantile regression is the wrong tool for these and the failure is not subtle.
# Matthew Stafford, Week 1: last ten games 3, 0, 3, 4, 2, 3, 2, 3, 2, 3, and the
# point model projects a rate of 2.61 which matches. The quantile CDF then put
# P(under 1.5) at 0.53 and the board recommended the under. A Poisson using the
# model's own 2.61 rate puts it at 0.265.
#
# That is not a question of which method scores better, it is a contradiction
# inside our own output: no distribution over non-negative integers can have a
# mean of 2.61 and a median of 1. Touchdowns run 0 to 4 or 5, the population
# median is 1, and separately-fitted conditional quantiles mostly reproduce the
# population shape instead of tracking a particular quarterback's rate.
#
# Honest caveat: on the graded picks the two methods are within noise of each
# other, because the odds history contains no pick above a 2.0 projected rate
# and so cannot adjudicate the case that prompted this. The argument for Poisson
# is that it is guaranteed consistent with the point model, and the point model
# is the validated one (pass_td holdout bias -1.5%, R2 0.167).
COUNT_MARKETS = {"pass_td", "rush_td", "rec_td", "any_td"}


def count_distribution(rate: float, line: float) -> tuple[float, dict]:
    """P(over) and a quantile set from a Poisson with the given rate.

    Book lines on these markets are half-integers, so `floor(line)` is the exact
    integer threshold and the survival function answers directly. No continuity
    correction, and no push case.
    """
    from scipy.stats import poisson as _poisson

    mu = max(float(rate), 1e-6)
    p_over = float(_poisson.sf(math.floor(float(line)), mu))
    qs = {q: float(_poisson.ppf(q, mu)) for q in (0.10, 0.25, 0.50, 0.75, 0.90)}
    return min(max(p_over, 0.02), 0.98), qs


def calibrated_quantiles(qpreds: dict, calibration=None) -> dict:
    """Re-read the fitted quantiles at the levels that make them honest.

    The same bias that makes P(over) wrong makes the displayed range wrong, and
    the range is the part a person actually looks at. If the raw q10 really
    covers 30% of outcomes, showing it as a 10th percentile is a lie about how
    wide the distribution is.

    Inverts the calibration map: for a target level t, find the raw level r with
    calib(r) = t, then read the fitted quantile function there. With no map
    present the inputs are returned untouched.
    """
    if not calibration:
        return dict(qpreds)
    levels = sorted(qpreds.keys())
    values = sorted(float(qpreds[q]) for q in levels)
    grid = np.asarray(calibration["grid"], dtype=float)
    mapped = np.asarray(calibration["level"], dtype=float)
    out = {}
    for t in levels:
        # np.interp needs an increasing x, and `mapped` is non-decreasing by
        # construction, so the inverse lookup is well defined.
        raw_level = float(np.interp(t, mapped, grid))
        out[t] = float(np.interp(raw_level, levels, values))
    return out


def load_quantile_bundle(artifact_dir: Path, market_code: str, lookback: int):
    """Load the quantile ensemble for a market, if one has been trained."""
    name = os.getenv("QUANT_MODEL_NAME", "quant_v1")
    path = artifact_dir / f"{name}_{market_code}_lb{lookback}.joblib"
    if not path.exists():
        return None
    bundle = joblib.load(path)
    print(f"  {market_code}: quantile intervals from {path.name}")
    return bundle


def load_model_meta(artifact_dir: Path, market_code: str, engine=None):
    """Load the model the DB currently marks active for this market.

    `active_models` is what `train.py` updates on every run, so it -- not a
    filename baked into this script -- is the source of truth for which model
    serves the dashboard. This used to hardcode `rf_posfilt_v4_*.json` per
    market, which meant retraining silently had no effect on the edges anyone
    actually saw. Falls back to scanning artifact metadata only if the market
    has no active row.
    """
    meta_name = None
    if engine is not None:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT a.model_name, a.lookback
                    FROM active_models a
                    JOIN prop_markets m ON m.id = a.market_id
                    WHERE m.code = :code
                    """
                ),
                {"code": market_code},
            ).mappings().first()
        if row:
            meta_name = f"{row['model_name']}_{market_code}_lb{row['lookback']}.json"

    if meta_name is None:
        return None

    meta_path = artifact_dir / meta_name
    if not meta_path.exists():
        print(f"  {market_code}: active model metadata {meta_name} not found, skipping")
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    artifact_path = artifact_dir / Path(meta["artifact_path"]).name
    if not artifact_path.exists():
        print(f"  {market_code}: artifact {artifact_path.name} not found, skipping")
        return None

    model = joblib.load(artifact_path)
    print(f"  {market_code}: using {meta['model_name']}")
    return meta, model


def main():
    engine = create_engine(DATABASE_URL, future=True)
    artifact_dir = Path(ARTIFACT_DIR)

    odds = pd.read_sql(
        text(
            """
            SELECT
              p.provider_event_id AS event_id,
              e.commence_time,
              e.home_team,
              e.away_team,
              p.player_name,
              p.market_key,
              p.bookmaker_key,
              p.bookmaker_title,
              p.line,
              MAX(CASE WHEN LOWER(p.outcome_name) = 'over' THEN p.price_american END) AS over_price,
              MAX(CASE WHEN LOWER(p.outcome_name) = 'under' THEN p.price_american END) AS under_price,
              MAX(p.last_update) AS source_last_update
            FROM odds_player_props p
            -- An INNER join, not a LEFT one. A prop whose event row is missing
            -- has no kickoff time, so there is no way to know whether the game
            -- has already been played, and it used to sail through with a NULL
            -- commence_time.
            JOIN odds_events e
              ON p.provider_event_id = e.provider_event_id
            WHERE p.line IS NOT NULL
              -- Only games that have not kicked off.
              --
              -- This table is supposed to hold the upcoming slate, and it does
              -- not: 1,581 of its rows are from December 2023, left behind by
              -- historical-odds work that wrote into the live table. Without
              -- this filter the dashboard published edges on games from two
              -- seasons ago, including a 98% "under 11.5 rush attempts" on a
              -- back whose 2023 role the model was reading from a stale window.
              -- Cleaning the table is worth doing separately; the builder
              -- should never have been able to price a finished game either
              -- way.
              AND e.commence_time > NOW()
            GROUP BY
              p.provider_event_id,
              e.commence_time,
              e.home_team,
              e.away_team,
              p.player_name,
              p.market_key,
              p.bookmaker_key,
              p.bookmaker_title,
              p.line
            """
        ),
        engine,
    )

    if odds.empty:
        raise RuntimeError("No rows in odds_player_props")

    pmf = pd.read_sql(
        text(
            """
            SELECT
              p.name AS player_name,
              pm.code AS market_code,
              pmf.player_id,
              pmf.market_id,
              pmf.as_of_game_date,
              pmf.opponent,
              pmf.lookback,
              pmf.mean,
              pmf.stddev,
              pmf.weighted_mean,
              pmf.trend,
              pmf.recs_mean,
              pmf.recs_trend,
              pmf.team,
              -- A player's team on an eight-month-old feature row is last
              -- season's team. players.team is the current one.
              p.team AS current_team,
              p.position AS position,
              pmf.aux_mean,
              pmf.aux_trend,
              pmf.extra_features
            FROM player_market_features pmf
            JOIN prop_markets pm
              ON pmf.market_id = pm.id
            JOIN players p
              ON pmf.player_id = p.external_id
            WHERE pmf.lookback = 5
            """
        ),
        engine,
    )

    if pmf.empty:
        raise RuntimeError("No rows in player_market_features")

    pmf["player_name_norm"] = pmf["player_name"].map(normalize_name)
    odds["player_name_norm"] = odds["player_name"].map(normalize_name)
    odds["market_code"] = odds["market_key"].map(ODDS_TO_MARKET)
    odds = odds[odds["market_code"].notna()].copy()
    pmf["as_of_game_date"] = pd.to_datetime(pmf["as_of_game_date"]).dt.date
    odds["event_date"] = pd.to_datetime(odds["commence_time"]).dt.date
    pmf["team_norm"] = pmf["team"].str.lower().map(TEAM_MAP)
    pmf["opponent_norm"] = pmf["opponent"].str.lower().map(TEAM_MAP)
    odds["home_team_norm"] = odds["home_team"].map(normalize_team)
    odds["away_team_norm"] = odds["away_team"].map(normalize_team)


    prob_cal = load_probability_calibrator(artifact_dir)
    ctx = load_current_context(engine)
    stale_factors = load_stale_role_factors(artifact_dir)

    model_cache = {}
    rows = []

    for _, o in odds.iterrows():
        market_code = o["market_code"]
        key = market_code

        if key not in model_cache:
            loaded = load_model_meta(artifact_dir, market_code, engine)
            model_cache[key] = (
                None
                if loaded is None
                else (
                    loaded[0],
                    loaded[1],
                    load_quantile_bundle(
                        artifact_dir, market_code, int(loaded[0].get("lookback", 5))
                    ),
                )
            )

        loaded = model_cache[key]
        if loaded is None:
            continue

        meta, model, quant = loaded

        player_market_rows = pmf[
            (pmf["player_name_norm"] == o["player_name_norm"]) &
            (pmf["market_code"] == market_code)
        ].copy()

        if player_market_rows.empty:
            continue

        # Use the most recent feature snapshot as of (and including) this game's date.
        # NOTE: each row's "opponent" column is simply who that historical game happened
        # to be against -- it is NOT a matchup key. Do not use it to search backward
        # through the player's history for "the last time they played this opponent";
        # that previously caused very old (sometimes 1-2 seasons stale, different-team)
        # rolling windows to be selected whenever the upcoming opponent happened to
        # repeat, producing wildly inflated projections. Opponent-specific signal is
        # already captured in this row's own opp_* extra_features, computed against
        # the real opponent for this exact as_of_game_date.
        candidates = player_market_rows[
            player_market_rows["as_of_game_date"] <= o["event_date"]
        ].copy()
        if candidates.empty:
            continue

        candidates = candidates.sort_values("as_of_game_date")

        latest_row = candidates.iloc[-1]

        # Sanity check: the player must actually be on one of the two teams in
        # this event.
        #
        # This deliberately uses his CURRENT team rather than the team on his
        # last feature row. Keying off history inverted the check's purpose: a
        # traded player -- exactly the case it was meant to catch -- was thrown
        # out because his old team was not in the matchup. David Montgomery had
        # 12 props offered for Houston and produced no edges at all, because his
        # feature rows still said Detroit. 94 players with live props had a
        # historical team different from their current one.
        current_team_norm = TEAM_MAP.get(
            str(latest_row.get("current_team") or "").lower()
        )
        player_team_norm = current_team_norm or latest_row["team_norm"]

        if player_team_norm not in (o["home_team_norm"], o["away_team_norm"]):
            continue

        match = candidates
        # Use the single most recent feature row, exactly as training does.
        # This previously averaged the base numeric columns over the last three
        # rows while taking extra_features from only the latest one -- both a
        # train/serve mismatch and a systematic source of overprojection, since
        # averaging drags older, hotter windows into a cooling player's line
        # (T.J. Hockenson 2023-12-10: true weighted_mean 73.2, but the 3-row
        # average fed the model 84.7).
        frow = match.iloc[-1].copy()

        feature_cols = meta.get("feature_cols", [])

        extra = frow.get("extra_features")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        elif extra is None:
            extra = {}
        elif not isinstance(extra, dict):
            extra = {}

        base_feature_values = {
            "mean": frow.get("mean", 0.0),
            "stddev": frow.get("stddev", 0.0),
            "weighted_mean": frow.get("weighted_mean", 0.0),
            "trend": frow.get("trend", 0.0),
            "aux_mean": frow.get("aux_mean", 0.0),
            "aux_trend": frow.get("aux_trend", 0.0),
            "recs_mean": frow.get("recs_mean", 0.0),
            "recs_trend": frow.get("recs_trend", 0.0),
        }

        def _num(v) -> float:
            """Coerce to a finite float, matching train.py's `_safe_float`.

            A feature can be present but null -- pre-game weather is unknown for
            a game that has not been scheduled into a forecast window yet, and a
            player's first rows have no aux stats. `dict.get(c, 0.0)` returns the
            stored None in that case rather than the default, and both the
            RandomForest and the quantile models reject NaN outright.
            """
            if v is None:
                return 0.0
            try:
                f = float(v)
            except (TypeError, ValueError):
                return 0.0
            return f if math.isfinite(f) else 0.0

        row_features = {}
        for c in feature_cols:
            if c in base_feature_values:
                row_features[c] = _num(base_feature_values[c])
            else:
                row_features[c] = _num(extra.get(c, 0.0))

        # as_of_game_date is the game the rolling window ends on.
        row_features["_last_game_date"] = frow.get("as_of_game_date")

        apply_current_context(
            row_features,
            ctx,
            player_id=frow.get("player_id"),
            team=(frow.get("current_team") or frow.get("team")),
            event_date=o["event_date"],
            position=frow.get("position"),
        )

        row_features.pop("_last_game_date", None)

        x = pd.DataFrame([row_features])

        model_projection_raw = float(model.predict(x)[0])

        if meta.get("target_transform") == "log1p":
            model_projection = math.expm1(model_projection_raw)
        else:
            model_projection = model_projection_raw

        model_projection = max(0.0, model_projection)

        # Apply the validated stale-role correction, if this market has one and
        # the player is actually in that state for this game.
        stale_factor = 1.0
        sf = stale_factors.get(market_code)
        if sf:
            demoted = row_features.get("depth_rank_delta", 0.0) or 0.0
            stale_days = row_features.get("days_since_last_game", 0.0) or 0.0
            if demoted >= 1 and stale_days > sf.get("stale_days", 60):
                stale_factor = float(sf["factor"])
                model_projection *= stale_factor

        weighted_mean = float(frow.get("weighted_mean", 0.0) or 0.0)

        # Serve the model's own prediction. This used to be
        # `0.3 * model + 0.7 * weighted_mean` clamped into
        # `weighted_mean +/- stddev`, which was never measured -- eval_blend.py
        # shows that blend discards more than half the model's edge over the
        # naive rolling average (rec_yds R2 0.402 -> 0.357 against a 0.319
        # baseline; same story for rush_yds and recs). Uncertainty now comes
        # from the quantile models rather than from clamping the point estimate.
        projection = model_projection

        line_value = float(o["line"])

        if quant is not None:
            # P(over) read off the predicted quantile CDF: uncertainty learned
            # per row from the features, rather than a single hand-capped sigma
            # applied to every player.
            # Scale the whole predicted distribution by the same factor, not
            # just the point estimate. Shrinking the projection while reading
            # P(over) off an unshrunk CDF would report a confidence that belongs
            # to a projection we no longer believe.
            qp = {
                q: max(0.0, float(qm.predict(x)[0])) * stale_factor
                for q, qm in quant["models"].items()
            }
            p_over = float(prob_over_from_quantiles(
                qp, line_value, quant.get("calibration")))
            p_under = 1.0 - p_over
            # The median, calibrated the same way the probability is.
            #
            # The side is chosen from the distribution, so the number shown
            # beside it has to come from the same distribution or the two
            # contradict each other. 59 of 283 graded picks read "model 75.6,
            # line 66.5, pick UNDER", which looks like a bug and is really the
            # mean of a right-skewed variable sitting well above its median.
            # For receiving yards the mean runs 25-35% above the median, so
            # showing the mean next to a median-based pick is just wrong.
            if market_code in COUNT_MARKETS:
                # Derive both the probability and the median from a
                # distribution whose mean is the point projection by
                # construction, so the two can never contradict each other.
                p_over, cal_q = count_distribution(projection, line_value)
                p_under = 1.0 - p_over
            else:
                cal_q = calibrated_quantiles(qp, quant.get("calibration"))
            median_value = float(cal_q.get(0.50, projection))
        else:
            # Fallback for markets with no quantile bundle yet.
            std = float(frow.get("stddev", 0.0) or 0.0)
            mean_value = float(frow.get("mean", 0.0))
            std_cap_base = max(weighted_mean, mean_value, line_value, 1.0)
            std = min(std, std_cap_base * 0.75)
            std = max(std, 1e-6)

            z = (line_value - projection) / std
            p_over = 1 - norm.cdf(z)
            p_under = norm.cdf(z)
            # A Gaussian is symmetric, so its median is its mean. Nothing to
            # correct, and nothing better available for this market.
            median_value = projection

        # Pick the side by expected value against the actual price, not by
        # which outcome is more likely.
        #
        # Taking the more likely side is not a strategy. On a -130 under the
        # break-even is 56.5%, so a 52% under is the more likely outcome and
        # still a losing bet, and roughly half the props on a slate are exactly
        # that shape. Backtested on the held-out 2025 season with models refit
        # on earlier seasons only, best of three books, bootstrapped by slate:
        #
        #   more-likely-side rule            -0.4% ROI
        #   EV > 2%                          +1.1%
        #   EV > 4%                          +2.0%
        #   EV > 8%                          +2.7%  95% CI [+0.2%, +5.1%]
        #   EV > 10%                         +4.1%  95% CI [+1.0%, +7.2%]
        #
        # Monotone all the way up, and the first result in this project whose
        # interval clears zero. Tiers are cut on EV for that reason.
        # Correct the probability before anything is decided from it. The side,
        # the expected value and the tier all derive from this number, so
        # calibrating afterwards would leave the filter selecting on the
        # inflated value it was supposed to fix.
        # Count markets skip this.
        #
        # The isotonic curve was fitted on probabilities that came off the
        # quantile CDF. A Poisson survival probability is a different quantity
        # produced a different way, and pushing it through a correction
        # estimated for the other method would distort a number that is already
        # consistent with the point model it came from.
        if market_code not in COUNT_MARKETS:
            p_over = calibrate_probability(prob_cal, market_code, float(p_over))
            p_under = 1.0 - p_over

        ev_over = float(p_over) - implied_prob(o["over_price"])
        ev_under = float(p_under) - implied_prob(o["under_price"])

        if ev_over >= ev_under:
            recommended_side = "over"
            win_prob = float(p_over)
            chosen_price = o["over_price"]
            expected_value = ev_over
            # Measured from the median for the same reason it is displayed:
            # an edge computed off the mean can point the opposite way to the
            # pick it is sitting next to.
            raw_edge = median_value - line_value
        else:
            recommended_side = "under"
            win_prob = float(p_under)
            chosen_price = o["under_price"]
            expected_value = ev_under
            raw_edge = line_value - median_value

        if not math.isfinite(expected_value):
            continue

        # Tiers re-cut for the calibrated probability.
        #
        # The old cuts (10/8/4/2) were chosen against the uninflated EV, so once
        # the probability was corrected downward by roughly fourteen points they
        # would have put almost nothing above "small". They were not working
        # anyway: graded on 6,301 picks, elite returned +2.3% while strong lost
        # 5.8% and medium lost 5.4%, which is not a ranking, it is noise wearing
        # four labels.
        #
        # These come from the measured return by calibrated EV on the held-out
        # 2025 season:
        #
        #   calibrated EV > 0%   n=1874   ROI +3.7%
        #   calibrated EV > 2%   n=1221   ROI +5.6%
        #   calibrated EV > 4%   n= 891   ROI +7.0%
        #   calibrated EV > 6%   n= 325   ROI +6.0%
        #
        # The return stops improving past 4%, so "elite" starts there rather
        # than chasing a higher cut on a thinner sample.
        # The over side has to clear a much higher bar.
        #
        # Measured on graded picks with the seasons used to choose kept separate
        # from the season used to verify, the under side is profitable and the
        # over side is not:
        #
        #                       2023-24        2025 (verification)
        #   unders only         +1.9%          +4.4%  CI [+1.8%, +7.2%]
        #   overs only          -7.1%          -4.7%  CI [-10.3%, +1.4%]
        #   board as published  -0.5%          +0.9%  CI [-1.7%, +4.0%]
        #
        # And no slice of the over side survives both periods. Elite overs lost
        # 10.9% in 2023-24 and made 3.3% in 2025; strong overs did the reverse.
        # Every market's overs are negative in 2025 except pass_td on 151 picks.
        #
        # This is the same asymmetry the whole project keeps running into: books
        # shade props toward the over because that is what the public buys, so
        # the over is where the price is worst and where our own errors cost the
        # most.
        #
        # Overs are not hidden, because suppressing data is worse than labelling
        # it. They are held to a threshold that reflects the fact that no
        # profitable over configuration has been verified, so an over can only
        # reach the top tiers when the model is far more emphatic than an under
        # would need to be.
        over = recommended_side == "over"
        cuts = ((0.12, 0.09, 0.06, 0.03) if over else (0.06, 0.04, 0.02, 0.0))

        if expected_value >= cuts[0]:
            tier = "elite"
        elif expected_value >= cuts[1]:
            tier = "strong"
        elif expected_value >= cuts[2]:
            tier = "medium"
        elif expected_value > cuts[3]:
            tier = "small"
        else:
            tier = "none"

        # A bet the price already covers is not an edge, so it is not shown.
        if tier == "none":
            continue
        
        rows.append({
            "event_id": o["event_id"],
            "commence_time": o["commence_time"],
            "home_team": o["home_team"],
            "away_team": o["away_team"],
            "player_name": o["player_name"],
            "market_code": market_code,
            "market_key": o["market_key"],
            "bookmaker_key": o["bookmaker_key"],
            "bookmaker_title": o["bookmaker_title"],
            "outcome_name": recommended_side,
            "line": float(o["line"]),
            "price_american": int(chosen_price) if pd.notna(chosen_price) else None,
            "model_name": meta["model_name"],
            "model_r2": float(meta.get("r2", 0.0)),
            "projection": projection,
            "projection_median": median_value,
            "raw_edge": raw_edge,
            "win_prob": win_prob,
            "expected_value": expected_value,
            "recommended_side": recommended_side,
            "edge_tier": tier,
            "market_id": int(frow["market_id"]),
            "lookback": int(frow["lookback"]),
            "source_last_update": o["source_last_update"],
            "notes": None,
        })

    if not rows:
        print("No edges generated")
        return

    out = pd.DataFrame(rows)

    # Value flag: under on a top-quartile line, in the markets where the effect
    # holds up across seasons.
    #
    # This is a STRUCTURAL flag, not a model signal, and after the walk-forward
    # calibration result it is the only edge in this project still standing. The
    # model contributes nothing on top of it: blind star-unders score 0.554
    # against model-filtered 0.558.
    #
    # What the market is doing: props are a recreational market, the money buys
    # overs, and the line gets pushed above the median until the over is a bad
    # bet. Vig is symmetric by construction, so the gap between the two sides of
    # the same market is shading. Measured blind, at best price, by season
    # (eval_over_shade.py):
    #
    #             over     under
    #   2023     -0.013   -0.034
    #   2024     -0.098   +0.043
    #   2025     -0.056   +0.005
    #
    # The over is negative in all three. That is the asymmetry, and it is the
    # part I trust.
    #
    # Top-quartile unders at best price, by season: +5.4%, +10.4%, +2.6%,
    # pooled +3.6% with a slate-clustered 95% interval of [+0.1%, +7.2%].
    # Positive in three independent seasons and the pooled interval clears zero.
    #
    # Two honest caveats. The interval clears zero by a hair, and 2025 alone
    # does not clear it. And the effect is shrinking every year (+10.4 -> +2.6),
    # which is what a market getting sharper looks like, so this is worth
    # re-measuring every season rather than trusting forever.
    #
    # Markets are whitelisted on the pooled three-season result at best price:
    # rush_yds +6.5% [+1.2%, +11.8%], recs +4.9% [+0.6%, +9.2%],
    # rush_att +8.0% [-0.7%, +15.9%] (kept on direction, n=159 is thin),
    # rec_yds +2.7% [-1.4%, +7.5%] (kept, weakest of the four).
    # pass_yds and pass_td are excluded: neither shows the effect.
    # The verified selection, marked before the structural value flag below.
    #
    # This is the only configuration in the project with a bootstrapped interval
    # that clears zero on a season never used to choose it: top tier, under
    # side, one pick per player-game. Chosen on 2023-24, verified on 2025 at
    # +7.1% per unit with a 95% interval of [+1.6%, +13.2%].
    #
    # Each condition was established on its own before being combined, so this
    # is three known effects stacked rather than a filter tuned until the number
    # looked good:
    #
    #   top tier      the tiers rank monotonically once the probability is
    #                 calibrated, elite +4.7% down to small -4.5%
    #   under side    the over side lost in both periods and no slice survived
    #   one per game  71.5% of player-games carried 2+ picks, and receptions and
    #                 receiving yards on the same player win and lose together
    #
    # Deliberately narrow. It marks a handful of rows a week, which is the point:
    # a board of sixty picks is a research tool, and this is the part of it that
    # has actually been shown to work.
    out["best_bet"] = False
    if len(out):
        eligible = out[
            (out["recommended_side"] == "under")
            & (out["edge_tier"].isin(["elite", "strong"]))
        ]
        if len(eligible):
            keep = (eligible.sort_values("expected_value", ascending=False)
                            .drop_duplicates(subset=["player_name", "market_code"])
                            .drop_duplicates(subset=["player_name",
                                                     "commence_time"]))
            out.loc[keep.index, "best_bet"] = True
        print(f"best-bet flagged {int(out['best_bet'].sum())} of {len(out)} edges")

    VALUE_MARKETS = {"recs", "rush_att", "rush_yds", "rec_yds"}
    out["value_flag"] = False
    if len(out):
        for mc, grp in out.groupby("market_code"):
            if mc not in VALUE_MARKETS or grp["line"].nunique() < 4:
                continue
            cut = grp["line"].quantile(0.75)
            mask = (
                (out["market_code"] == mc)
                & (out["recommended_side"] == "under")
                & (out["line"] >= cut)
            )
            out.loc[mask, "value_flag"] = True
        print(f"value-flagged {int(out['value_flag'].sum())} of {len(out)} edges")


    with engine.begin() as conn:
        # prop_edge_results is intentionally NOT cascaded: the graded track
        # record has to survive edge rebuilds, which happen on every run.
        conn.execute(text("TRUNCATE prop_edges"))
    out.to_sql("prop_edges", engine, if_exists="append", index=False, method="multi", chunksize=500)

    print(f"PROP EDGES BUILT: {len(out)} rows")


if __name__ == "__main__":
    main()