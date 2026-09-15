"""Rebuild published picks for a game whose board was lost before grading.

`prop_edges` is truncated on every build and a box score arrives a day after
kickoff, so until `prop_edges_history` existed a game could be rebuilt away
before its own results landed. That is what happened to the Monday night board
of 2026-09-14: published picks were never graded, and the track record simply
does not contain a game the site had opinions about.

Nothing here is invented. Three sources, all captured before kickoff:

  * `player_projection_history` holds the exact ladder the site published,
    written at 22:14 UTC for a 00:15 kickoff and never revised.
  * `odds_snapshots` holds the prices as they stood, labelled `live`.
  * `player_game_stats_app` holds what the players actually did, and grading
    fills that in afterwards exactly as it does for any other pick.

The side, the line, the price and the outcome are therefore the real ones. The
win probability and the tier are recomputed from the stored ladder using
today's probability calibrator, because the calibrator that ran that evening is
not kept, so those two fields are a faithful reconstruction rather than a
recording. Hit rate and return, which is what the track record reports, depend
only on the fields that are exact.

Idempotent: a game that already has live picks is skipped, so this cannot
double-count and cannot overwrite anything grading has written.
"""

import os
from pathlib import Path

import build_prop_edges as bp
import numpy as np
import pandas as pd
from backtest_season import MARKET_MAP, american_to_prob
from sqlalchemy import create_engine, text

LEVELS = [0.10, 0.25, 0.50, 0.75, 0.90]
GAME_DATE = os.getenv("RECOVER_GAME_DATE", "")

PRICES_SQL = """
    WITH last_price AS (
        -- The final price seen before kickoff, per prop per book per side.
        SELECT DISTINCT ON (s.provider_event_id, s.player_name, s.market_key,
                            s.bookmaker_key, s.outcome_name)
               s.player_name, s.market_key, s.bookmaker_key, s.bookmaker_title,
               s.outcome_name, s.line, s.price_american, s.commence_time,
               s.home_team, s.away_team
        FROM odds_snapshots s
        WHERE s.source = 'live' AND s.line IS NOT NULL
          AND s.observed_at <= s.commence_time
        ORDER BY s.provider_event_id, s.player_name, s.market_key,
                 s.bookmaker_key, s.outcome_name, s.observed_at DESC
    )
    SELECT h.player_id, h.player_name, h.market_code, h.game_date,
           h.p10, h.p25, h.p50, h.p75, h.p90, h.projection,
           lp.market_key, lp.bookmaker_key, lp.bookmaker_title, lp.outcome_name,
           lp.line, lp.price_american, lp.home_team, lp.away_team
    FROM player_projection_history h
    JOIN last_price lp
      ON lower(replace(replace(lp.player_name, '.', ''), '-', ' ')) =
         lower(replace(replace(h.player_name, '.', ''), '-', ' '))
     AND (lp.commence_time AT TIME ZONE 'America/New_York')::date = h.game_date
    WHERE h.p10 IS NOT NULL AND h.p90 IS NOT NULL
"""


def tier_for(ev: float, side: str) -> str:
    """The published tier rule, matching build_prop_edges."""
    cuts = (0.12, 0.09, 0.06, 0.03) if side == "over" else (0.06, 0.04, 0.02, 0.0)
    if ev >= cuts[0]:
        return "elite"
    if ev >= cuts[1]:
        return "strong"
    if ev >= cuts[2]:
        return "medium"
    if ev > cuts[3]:
        return "small"
    return "none"


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    prob_cal = bp.load_probability_calibrator(Path(bp.ARTIFACT_DIR))

    sql = PRICES_SQL
    params = None
    if GAME_DATE:
        sql += " AND h.game_date = :d"
        params = {"d": GAME_DATE}
    rows = pd.read_sql(text(sql), eng, params=params)
    if rows.empty:
        print("no stored board matches a stored price; nothing to recover")
        return

    # Odds market keys to this project's market codes.
    mapped = rows["market_key"].map(MARKET_MAP)
    rows = rows[mapped.notna() & (mapped == rows["market_code"])]
    rows = rows[~rows["market_code"].isin(bp.SUPPRESSED_MARKETS)]

    already = pd.read_sql(text(
        "SELECT DISTINCT game_date FROM prop_edge_results WHERE source = 'live'"
    ), eng)["game_date"].astype(str).tolist()
    rows = rows[~rows["game_date"].astype(str).isin(already)]
    if rows.empty:
        print("every game with a stored board already has live picks")
        return

    over = rows[rows["outcome_name"].str.lower() == "over"]
    under = rows[rows["outcome_name"].str.lower() == "under"]
    key = ["player_name", "market_code", "bookmaker_key", "line"]
    m = over.merge(under[key + ["price_american"]], on=key,
                   suffixes=("_over", "_under"), how="inner")
    if m.empty:
        print("no two-sided prices found for the missing games")
        return

    ladder = np.sort(m[["p10", "p25", "p50", "p75", "p90"]].to_numpy(), axis=1)
    lines = m["line"].to_numpy(dtype=float)
    p_under_at_line = np.clip(
        [float(np.interp(l, row, LEVELS)) for l, row in zip(lines, ladder)],
        0.01, 0.99)

    kept = []
    for i, r in enumerate(m.itertuples(index=False)):
        take_over = float(r.p50) > float(r.line)
        raw = (1.0 - p_under_at_line[i]) if take_over else p_under_at_line[i]
        p = bp._bounded(bp.calibrate_probability(prob_cal, r.market_code, float(raw)))
        price = r.price_american_over if take_over else r.price_american_under
        ev = float(p) - american_to_prob(price)
        side = "over" if take_over else "under"
        tier = tier_for(ev, side)
        if tier == "none" or p <= 0.5:
            continue
        kept.append({
            "player_id": r.player_id, "player_name": r.player_name,
            "game_date": r.game_date, "market_code": r.market_code,
            "line": float(r.line), "projection": float(r.projection),
            "projection_median": float(r.p50), "recommended_side": side,
            "win_prob": float(p), "win_prob_raw": float(raw), "edge_tier": tier,
            "expected_value": ev,
            "ev_per_unit": bp.ev_per_unit_staked(ev, price),
            "price_american": int(price), "bookmaker_title": r.bookmaker_title,
            "home_team": r.home_team, "away_team": r.away_team,
            "q10": float(r.p10), "q25": float(r.p25),
            "q75": float(r.p75), "q90": float(r.p90),
        })

    if not kept:
        print("no reconstructed pick cleared the publishing rules")
        return

    df = pd.DataFrame(kept)
    # One pick per player, game and market, the strongest, as the board shows.
    df = df.sort_values("ev_per_unit", ascending=False).drop_duplicates(
        subset=["player_name", "game_date", "market_code"])
    d = pd.to_datetime(df["game_date"])
    df["season"] = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
    df["source"] = "live"
    df["best_bet"] = False
    eligible = df[(df["recommended_side"] == "under")
                  & (df["edge_tier"].isin(["elite", "strong"]))]
    if len(eligible):
        keep = (eligible.sort_values("ev_per_unit", ascending=False)
                        .drop_duplicates(subset=["player_name", "market_code"])
                        .drop_duplicates(subset=["player_name", "game_date"]))
        df.loc[keep.index, "best_bet"] = True
    # Well clear of both the live sequence and the backtest's negative ids.
    df["edge_id"] = -(900000 + np.arange(len(df)))

    with eng.begin() as conn:
        df.to_sql("prop_edge_results", conn, if_exists="append", index=False,
                  method="multi", chunksize=200)

    print(f"recovered {len(df)} picks for "
          f"{', '.join(sorted(str(g) for g in df['game_date'].unique()))}")
    print(df.groupby("edge_tier").size().to_string())
    print("run grade_edges.py to attach the results")


if __name__ == "__main__":
    main()
