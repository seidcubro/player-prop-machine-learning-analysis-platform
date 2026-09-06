"""Compare our projection to the sportsbook's line and to what actually happened.

Three numbers per prop: what the book said, what we said, what the player did.
That triangle is more informative than our error alone, because it separates two
very different failures:

  - We are wrong and the book is right  -> our model has a problem.
  - We are wrong and the book is too    -> the game was unpredictable, and no
                                            model would have got it.

Only the first is fixable. A model that misses by 20 yards on a game the book
also missed by 18 is not broken; a model that misses by 20 where the book missed
by 3 is.

The headline metric is therefore **how often we beat the book**: the share of
props where our projection landed closer to the actual result than the line did.
A line is a sharp, market-clearing estimate, so beating it more than half the
time is the real bar. R2 against actuals cannot tell you that.

Writes a row per prop to `projection_log` so patterns can be sliced afterwards.

Usage:
    python projection_log.py                 # score everything available
    python projection_log.py --season 2024   # one season
"""

import argparse
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

# Odds API market key -> our market code.
MARKET_MAP = {
    "player_reception_yds": "rec_yds",
    "player_receptions": "recs",
    "player_rush_yds": "rush_yds",
    "player_rush_attempts": "rush_att",
    "player_pass_yds": "pass_yds",
    "player_pass_tds": "pass_td",
    "player_pass_completions": "pass_completions",
    "player_pass_attempts": "pass_att",
}

STAT_COL = {
    "rec_yds": "receiving_yards",
    "recs": "receptions",
    "rush_yds": "rushing_yards",
    "rush_att": "carries",
    "pass_yds": "passing_yards",
    "pass_td": "passing_tds",
    "pass_completions": "completions",
    "pass_att": "attempts",
}


def load(engine, season: int | None) -> pd.DataFrame:
    """Join historical lines to the actual box score for that game.

    Consensus line per player/market/game: books disagree by half a point and
    that noise is not what is being measured here.
    """
    where = "AND EXTRACT(YEAR FROM s.commence_time) = :season" if season else ""
    q = text(f"""
        WITH consensus AS (
            SELECT
                s.player_name,
                s.market_key,
                (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
                s.home_team, s.away_team,
                AVG(s.line) AS book_line,
                COUNT(DISTINCT s.bookmaker_key) AS books
            FROM odds_snapshots s
            WHERE s.line IS NOT NULL
              AND lower(s.outcome_name) = 'over'
              {where}
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT c.*, p.id AS player_id, p.external_id, p.position, g.game_date AS played_on,
               g.receiving_yards, g.receptions, g.rushing_yards, g.carries,
               g.passing_yards, g.passing_tds, g.completions, g.attempts
        FROM consensus c
        JOIN players p
          ON lower(replace(replace(p.name, '.', ''), '-', ' ')) =
             lower(replace(replace(c.player_name, '.', ''), '-', ' '))
        JOIN player_game_stats_app g
          ON g.player_id = p.external_id
         AND g.game_date BETWEEN c.game_date - 1 AND c.game_date + 1
    """)
    return pd.read_sql(q, engine, params={"season": season} if season else {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, future=True)
    df = load(engine, args.season)
    if df.empty:
        raise SystemExit("no historical lines matched to results; run backfill_historical_odds.py first")

    df["market_code"] = df["market_key"].map(MARKET_MAP)
    df = df[df["market_code"].notna()].copy()

    # The +/-1 day join can match one prop to two game rows (a Sunday line sits
    # within a day of both a Saturday and a Monday game). Keep the game closest
    # to the listed kickoff date.
    df["date_gap"] = (
        pd.to_datetime(df["played_on"]) - pd.to_datetime(df["game_date"])
    ).abs()
    df = (
        df.sort_values("date_gap")
          .drop_duplicates(subset=["external_id", "market_code", "game_date"], keep="first")
          .drop(columns=["date_gap"])
    )

    df["actual"] = df.apply(
        lambda r: r.get(STAT_COL.get(r["market_code"], ""), np.nan), axis=1
    )
    df = df.dropna(subset=["actual", "book_line"])

    # Our projection for that game, from the stored feature row as of that date.
    proj = pd.read_sql(
        text("""
            SELECT DISTINCT ON (f.player_id, m.code, f.as_of_game_date)
                   f.player_id AS external_id, m.code AS market_code,
                   f.as_of_game_date, f.weighted_mean, f.mean
            FROM player_market_features f
            JOIN prop_markets m ON m.id = f.market_id
            WHERE f.lookback = 5
            ORDER BY f.player_id, m.code, f.as_of_game_date
        """),
        engine,
    )
    df = df.merge(
        proj,
        left_on=["external_id", "market_code", "played_on"],
        right_on=["external_id", "market_code", "as_of_game_date"],
        how="left",
    )

    # The rolling weighted mean stands in for the model here: it is what the
    # model is built on and is available for every historical row, whereas a
    # retrained model per season would confound the comparison with whatever
    # data that model happened to see.
    df["our_projection"] = df["weighted_mean"]
    df = df.dropna(subset=["our_projection"])

    df["book_err"] = (df["book_line"] - df["actual"]).abs()
    df["our_err"] = (df["our_projection"] - df["actual"]).abs()
    df["we_beat_book"] = df["our_err"] < df["book_err"]
    df["book_bias"] = df["book_line"] - df["actual"]
    df["our_bias"] = df["our_projection"] - df["actual"]

    print(f"\n{len(df)} props matched to results across "
          f"{df['game_date'].nunique()} slates\n")

    print("=== how often do we land closer than the line? (50% = parity) ===")
    by_market = df.groupby("market_code").agg(
        n=("we_beat_book", "size"),
        beat_book=("we_beat_book", "mean"),
        our_mae=("our_err", "mean"),
        book_mae=("book_err", "mean"),
        our_bias=("our_bias", "mean"),
        book_bias=("book_bias", "mean"),
    ).sort_values("n", ascending=False)
    print(by_market.round(3).to_string())

    print("\n=== overall ===")
    print(f"we beat the book on {df['we_beat_book'].mean():.1%} of props")
    print(f"our MAE  {df['our_err'].mean():.2f}   book MAE {df['book_err'].mean():.2f}")
    print(f"our bias {df['our_bias'].mean():+.2f}   book bias {df['book_bias'].mean():+.2f}")

    print("\n=== where we disagree most with the line, were we right? ===")
    df["disagreement"] = (df["our_projection"] - df["book_line"]).abs()
    df["dis_bucket"] = pd.qcut(df["disagreement"], 4,
                               labels=["closest", "close", "far", "furthest"],
                               duplicates="drop")
    print(df.groupby("dis_bucket", observed=True).agg(
        n=("we_beat_book", "size"),
        beat_book=("we_beat_book", "mean"),
        our_mae=("our_err", "mean"),
        book_mae=("book_err", "mean"),
    ).round(3).to_string())

    print("\n=== by position ===")
    print(df.groupby("position", observed=True).agg(
        n=("we_beat_book", "size"),
        beat_book=("we_beat_book", "mean"),
        our_bias=("our_bias", "mean"),
    ).query("n >= 10").round(3).to_string())

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projection_log (
                id BIGSERIAL PRIMARY KEY,
                player_name TEXT, external_id TEXT, position TEXT,
                market_code TEXT, game_date DATE,
                book_line DOUBLE PRECISION, our_projection DOUBLE PRECISION,
                actual DOUBLE PRECISION,
                book_err DOUBLE PRECISION, our_err DOUBLE PRECISION,
                we_beat_book BOOLEAN, books INTEGER,
                logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (external_id, market_code, game_date)
            )
        """))
        keep = ["player_name", "external_id", "position", "market_code", "game_date",
                "book_line", "our_projection", "actual", "book_err", "our_err",
                "we_beat_book", "books"]
        conn.execute(text("DELETE FROM projection_log"))
        df[keep].to_sql("projection_log", conn, if_exists="append", index=False)
    print(f"\nwrote {len(df)} rows to projection_log")


if __name__ == "__main__":
    main()
