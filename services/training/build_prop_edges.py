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


def prob_over_from_quantiles(qpreds: dict, line: float) -> float:
    """P(outcome > line), interpolated from predicted quantiles.

    The fitted quantiles are CDF points, so interpolating the level at `line`
    gives P(outcome <= line) directly. Values are sorted first because
    separately-fitted quantile models can cross, which would make the CDF
    non-monotonic. Clipped away from 0/1 -- no prop is ever a certainty, and a
    sportsbook line sitting outside the whole predicted range still should not
    read as 100%.
    """
    levels = sorted(qpreds.keys())
    values = sorted(qpreds[q] for q in levels)
    p_under = float(np.interp(line, values, levels))
    return float(min(max(1.0 - p_under, 0.02), 0.98))


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
            LEFT JOIN odds_events e
              ON p.provider_event_id = e.provider_event_id
            WHERE p.line IS NOT NULL
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
            p_over = float(prob_over_from_quantiles(qp, line_value))
            p_under = 1.0 - p_over
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

        if p_over >= p_under:
            recommended_side = "over"
            win_prob = float(p_over)
            chosen_price = o["over_price"]
            raw_edge = projection - line_value
        else:
            recommended_side = "under"
            win_prob = float(p_under)
            chosen_price = o["under_price"]
            raw_edge = line_value - projection

        prob_edge = abs(win_prob - 0.5)

        if prob_edge >= 0.20:
            tier = "elite"
        elif prob_edge >= 0.15:
            tier = "strong"
        elif prob_edge >= 0.10:
            tier = "medium"
        elif prob_edge >= 0.05:
            tier = "small"
        else:
            tier = "none"

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
            "raw_edge": raw_edge,
            "win_prob": win_prob,
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

    with engine.begin() as conn:
        # prop_edge_results is intentionally NOT cascaded: the graded track
        # record has to survive edge rebuilds, which happen on every run.
        conn.execute(text("TRUNCATE prop_edges"))
    out.to_sql("prop_edges", engine, if_exists="append", index=False, method="multi", chunksize=500)

    print(f"PROP EDGES BUILT: {len(out)} rows")


if __name__ == "__main__":
    main()