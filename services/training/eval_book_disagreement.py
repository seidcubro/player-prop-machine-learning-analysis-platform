"""Does one book being out of line with the others predict anything?

This is the last untested idea, and unlike everything else in this project it
does not need the model to be any good. The signal isn't "my projection
disagrees with the market", which has now failed three ways. It's "the books
disagree with each other".

If DraftKings hangs 45.5 while FanDuel and BetMGM both sit at 48.5, one of two
things is true. Either DraftKings is slow and the number is stale, in which case
taking the over at the cheaper number is value, or DraftKings knows something the
others don't. Which of those dominates is an empirical question and nobody has
asked it here yet.

Method: for every prop, take each book's line and compare it to the median of the
*other* books (leave-one-out, so a book is never compared against itself). Where
a book is off by at least a threshold, bet the side its number favours, and grade
against what actually happened.

An outlier low line makes the over cheap. An outlier high line makes the under
cheap. Both are graded at that book's own price, since that is the bet you would
actually be able to place.
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

MARKET_MAP = {
    "player_reception_yds": "receiving_yards",
    "player_receptions": "receptions",
    "player_rush_yds": "rushing_yards",
    "player_rush_attempts": "carries",
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
}


def main():
    engine = create_engine(DATABASE_URL, future=True)

    stat_case = "\n".join(
        f"            WHEN b.market_key = '{k}' THEN g.{v}" for k, v in MARKET_MAP.items()
    )

    df = pd.read_sql(
        text(f"""
        WITH per_book AS (
            SELECT s.provider_event_id, s.player_name, s.market_key,
                   (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
                   s.bookmaker_key,
                   MAX(s.line) FILTER (WHERE lower(s.outcome_name) = 'over') AS line,
                   MAX(CASE WHEN s.price_american < 0
                            THEN 1 + 100.0 / (-s.price_american)
                            ELSE 1 + s.price_american / 100.0 END)
                     FILTER (WHERE lower(s.outcome_name) = 'over')  AS over_dec,
                   MAX(CASE WHEN s.price_american < 0
                            THEN 1 + 100.0 / (-s.price_american)
                            ELSE 1 + s.price_american / 100.0 END)
                     FILTER (WHERE lower(s.outcome_name) = 'under') AS under_dec
            FROM odds_snapshots s
            WHERE s.line IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5
        )
        SELECT b.*,
               CASE
{stat_case}
               END::float8 AS actual
        FROM per_book b
        JOIN players p
          ON lower(replace(replace(p.name, '.', ''), '-', ' ')) =
             lower(replace(replace(b.player_name, '.', ''), '-', ' '))
        JOIN player_game_stats_app g
          ON g.player_id = p.external_id
         AND g.game_date BETWEEN b.game_date - 1 AND b.game_date + 1
        WHERE b.line IS NOT NULL
        """),
        engine,
    )
    df = df.dropna(subset=["line", "actual"])
    df = df.drop_duplicates(
        subset=["provider_event_id", "player_name", "market_key", "bookmaker_key"]
    )
    print(f"{len(df)} book-level quotes with a known result\n")

    # Leave-one-out: each book against the median of the others on the same prop.
    key = ["provider_event_id", "player_name", "market_key"]
    grp = df.groupby(key)["line"]
    df["n_books"] = grp.transform("size")
    df = df[df["n_books"] >= 3].copy()
    df["sum_line"] = grp.transform("sum")
    df["others_mean"] = (df["sum_line"] - df["line"]) / (df["n_books"] - 1)
    df["gap"] = df["line"] - df["others_mean"]

    # An outlier-low line makes the over the cheap side, and vice versa.
    df["side"] = np.where(df["gap"] < 0, "over", "under")
    df["hit"] = np.where(
        df["actual"] == df["line"], np.nan,
        np.where(df["side"] == "over",
                 df["actual"] > df["line"], df["actual"] < df["line"]),
    )
    df["decimal"] = np.where(df["side"] == "over",
                             df["over_dec"].fillna(1.909),
                             df["under_dec"].fillna(1.909))

    g = df[df["hit"].notna()].copy()
    g["breakeven"] = 1.0 / g["decimal"]
    g["units"] = np.where(g["hit"].astype(bool), g["decimal"] - 1.0, -1.0)
    g["absgap"] = g["gap"].abs()

    print(f"{'threshold':<14}{'n':>7}{'hit':>8}{'break-even':>12}{'edge':>8}{'ROI':>9}")
    print("-" * 58)
    for t in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
        sub = g[g["absgap"] >= t]
        if len(sub) < 50:
            continue
        edge = sub["hit"].mean() - sub["breakeven"].mean()
        print(f">= {t:<11.1f}{len(sub):>7}{sub['hit'].mean():>8.3f}"
              f"{sub['breakeven'].mean():>12.3f}{edge:>+8.3f}{sub['units'].mean():>+9.3f}")

    print("\n=== by book, at a gap of at least 1.0 ===")
    sub = g[g["absgap"] >= 1.0]
    if len(sub) >= 50:
        by = sub.groupby("bookmaker_key").agg(
            n=("hit", "size"), hit=("hit", "mean"),
            breakeven=("breakeven", "mean"), roi=("units", "mean"),
        )
        by["edge"] = by["hit"] - by["breakeven"]
        print(by.round(3).to_string())

    print("\n=== by market, at a gap of at least 1.0 ===")
    if len(sub) >= 50:
        bm = sub.groupby("market_key").agg(
            n=("hit", "size"), hit=("hit", "mean"),
            breakeven=("breakeven", "mean"), roi=("units", "mean"),
        )
        bm["edge"] = bm["hit"] - bm["breakeven"]
        print(bm.round(3).to_string())

    best = g[g["absgap"] >= 1.0]
    if len(best) >= 50:
        e = best["hit"].mean() - best["breakeven"].mean()
        print(f"\noutlier-book edge at gap >= 1.0: {e:+.3f} "
              f"over {len(best)} bets, ROI {best['units'].mean():+.3%}")


if __name__ == "__main__":
    main()
