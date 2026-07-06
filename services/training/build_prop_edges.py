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


def load_model_meta(artifact_dir: Path, market_code: str):
    # All markets trained by the same rf_posfilt_v4 run: eligible_positions
    # filtering (train.py/eval.py no longer dilute training with rows from
    # positions that never produce the stat) plus jobs.py feature additions
    # -- snap share, nflverse ff_opportunity expected usage, and current-game
    # Vegas context (spread/total/weather/injury status) -- on top of the
    # original rolling box-score features.
    candidates = {
        "pass_att": "rf_posfilt_v4_pass_att_lb5.json",
        "pass_yds": "rf_posfilt_v4_pass_yds_lb5.json",
        "pass_completions": "rf_posfilt_v4_pass_completions_lb5.json",
        "pass_td": "rf_posfilt_v4_pass_td_lb5.json",
        "rush_att": "rf_posfilt_v4_rush_att_lb5.json",
        "rush_yds": "rf_posfilt_v4_rush_yds_lb5.json",
        "rush_td": "rf_posfilt_v4_rush_td_lb5.json",
        "recs": "rf_posfilt_v4_recs_lb5.json",
        "rec_yds": "rf_posfilt_v4_rec_yds_lb5.json",
        "rec_td": "rf_posfilt_v4_rec_td_lb5.json",
    }
    meta_name = candidates.get(market_code)
    if not meta_name:
        return None

    meta_path = artifact_dir / meta_name
    if not meta_path.exists():
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    artifact_path = artifact_dir / Path(meta["artifact_path"]).name
    if not artifact_path.exists():
        return None

    model = joblib.load(artifact_path)
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


    model_cache = {}
    rows = []

    for _, o in odds.iterrows():
        market_code = o["market_code"]
        key = market_code

        if key not in model_cache:
            loaded = load_model_meta(artifact_dir, market_code)
            model_cache[key] = loaded

        loaded = model_cache[key]
        if loaded is None:
            continue

        meta, model = loaded

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
        player_team_norm = latest_row["team_norm"]

        # sanity check: the player's most recently known team should be one of the
        # two teams in this event, otherwise this is likely a stale/traded player.
        if player_team_norm not in (o["home_team_norm"], o["away_team_norm"]):
            continue

        match = candidates
        recent_rows = match.tail(3)
        frow = recent_rows.iloc[-1].copy()

        numeric_cols = [
            "mean",
            "weighted_mean",
            "trend",
            "stddev",
            "aux_mean",
            "aux_trend",
            "recs_mean",
            "recs_trend"
        ]

        for col in numeric_cols:
            if col in recent_rows.columns:
                frow[col] = recent_rows[col].mean()

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

        row_features = {}
        for c in feature_cols:
            if c in base_feature_values:
                row_features[c] = base_feature_values[c]
            else:
                row_features[c] = extra.get(c, 0.0)

        x = pd.DataFrame([row_features])

        model_projection_raw = float(model.predict(x)[0])

        if meta.get("target_transform") == "log1p":
            model_projection = math.expm1(model_projection_raw)
        else:
            model_projection = model_projection_raw

        model_projection = max(0.0, model_projection)

        weighted_mean = float(frow.get("weighted_mean", 0.0) or 0.0)

        projection = 0.3 * model_projection + 0.7 * weighted_mean

        std = float(frow.get("stddev", 0.0))
        std = min(std, weighted_mean * 0.75)

        upper_bound = weighted_mean + 1.0 * std
        lower_bound = max(0.0, weighted_mean - 1.0 * std)

        projection = max(lower_bound, min(projection, upper_bound))
        
        line_value = float(o["line"])

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
        conn.execute(text("TRUNCATE prop_edges"))
    out.to_sql("prop_edges", engine, if_exists="append", index=False, method="multi", chunksize=500)

    print(f"PROP EDGES BUILT: {len(out)} rows")


if __name__ == "__main__":
    main()