"""Closing line value: did the number I took beat the number the market closed at?

Win/loss is a terrible short-run signal. At a ~53% break-even, several hundred
bets is still mostly variance, and a season of props might not settle the
question either way.

CLV settles it much faster. If I take a receiver at 45.5 and the line closes at
48.5, I got a better number than the final market consensus, and over enough bets
that shows up as profit regardless of how any individual game went. Beating the
close consistently is the standard evidence that a bettor has real edge; losing to
it consistently is proof they do not, long before the balance says so.

This needs `odds_snapshots` to hold more than one observation per prop, which is
what the twice-weekly capture in scripts/weekly_update.sh is for. With a single
snapshot there is no open and no close to compare.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)


def main():
    engine = create_engine(DATABASE_URL, future=True)

    df = pd.read_sql(
        text("""
            WITH obs AS (
                SELECT provider_event_id, player_name, market_key, bookmaker_key,
                       commence_time, observed_at, line,
                       ROW_NUMBER() OVER (
                           PARTITION BY provider_event_id, player_name,
                                        market_key, bookmaker_key
                           ORDER BY observed_at
                       ) AS first_seen,
                       ROW_NUMBER() OVER (
                           PARTITION BY provider_event_id, player_name,
                                        market_key, bookmaker_key
                           ORDER BY observed_at DESC
                       ) AS last_seen
                FROM odds_snapshots
                WHERE lower(outcome_name) = 'over' AND line IS NOT NULL
            )
            SELECT o.provider_event_id, o.player_name, o.market_key,
                   o.bookmaker_key, o.commence_time,
                   MAX(CASE WHEN o.first_seen = 1 THEN o.line END) AS open_line,
                   MAX(CASE WHEN o.last_seen  = 1 THEN o.line END) AS close_line,
                   MIN(o.observed_at) AS opened_at,
                   MAX(o.observed_at) AS closed_at
            FROM obs o
            GROUP BY 1, 2, 3, 4, 5
            HAVING COUNT(*) > 1
        """),
        engine,
    )

    if df.empty:
        print("No prop has more than one snapshot yet, so there is no open and")
        print("no close to compare. Run scripts/weekly_update.sh --close-only")
        print("shortly before kickoff to start building that history.")
        return

    df["move"] = df["close_line"] - df["open_line"]
    print(f"{len(df)} props with an open and a close\n")

    print("=== how much do these lines actually move? ===")
    print(f"  moved at all : {(df['move'].abs() > 0).mean():.1%}")
    print(f"  mean |move|  : {df['move'].abs().mean():.2f}")
    print(f"  median |move|: {df['move'].abs().median():.2f}")
    print(f"  90th pct     : {df['move'].abs().quantile(0.9):.2f}")

    edges = pd.read_sql(
        text("""
            SELECT r.player_name, r.market_code, r.game_date, r.line AS taken_line,
                   r.recommended_side, r.hit
            FROM prop_edge_results r
            WHERE r.hit IS NOT NULL
        """),
        engine,
    )
    if edges.empty:
        print("\nNo graded picks yet, so CLV cannot be attributed to them.")
        return

    # Every market the odds sync requests. A missing key here silently drops
    # those picks from the CLV result rather than erroring, so it has to stay in
    # step with services/api/app/odds_market_map.py.
    MARKET_MAP = {
        "player_reception_yds": "rec_yds",
        "player_receptions": "recs",
        "player_rush_yds": "rush_yds",
        "player_rush_attempts": "rush_att",
        "player_pass_yds": "pass_yds",
        "player_pass_tds": "pass_td",
        "player_pass_attempts": "pass_att",
        "player_pass_completions": "pass_completions",
        "player_anytime_td": "any_td",
    }
    df["market_code"] = df["market_key"].map(MARKET_MAP)
    df["game_date"] = pd.to_datetime(df["commence_time"]).dt.date

    close = (
        df.dropna(subset=["market_code"])
          .groupby(["player_name", "market_code", "game_date"], as_index=False)
          ["close_line"].mean()
    )
    edges["game_date"] = pd.to_datetime(edges["game_date"]).dt.date
    m = edges.merge(close, on=["player_name", "market_code", "game_date"], how="inner")
    if m.empty:
        print("\nNo graded pick lines up with a captured close yet.")
        return

    # Beating the close means taking a number the market later moved away from,
    # in the direction of the side I was on.
    m["beat_close"] = np.where(
        m["recommended_side"] == "over",
        m["taken_line"] < m["close_line"],
        m["taken_line"] > m["close_line"],
    )
    m["clv_points"] = np.where(
        m["recommended_side"] == "over",
        m["close_line"] - m["taken_line"],
        m["taken_line"] - m["close_line"],
    )

    print(f"\n=== closing line value on {len(m)} graded picks ===")
    print(f"  beat the close : {m['beat_close'].mean():.1%}")
    print(f"  mean CLV       : {m['clv_points'].mean():+.2f} points")
    print("\n=== did beating the close predict winning? ===")
    print(m.groupby("beat_close").agg(
        n=("hit", "size"), hit_rate=("hit", "mean"),
    ).round(3).to_string())
    print("\nBeating the close should show a higher hit rate. If it does not over")
    print("a real sample, the picks are not finding value the market later agrees")
    print("with, whatever the win/loss record happens to say.")


if __name__ == "__main__":
    main()
