"""Is the market itself wrong anywhere? Tested without the model.

Our projections add nothing the price does not already carry: predicting
outcomes from the price alone beats predicting them from the price plus the
model, out of sample, in every market. So a better model is not the lever. The
only thing left that could pay is a market that is wrong in a way that repeats.

That is a claim about sportsbook lines, not about us, so this tests it with no
model involved at all. Every prop a book posted, one row per player, market and
game, taken blind:

    always the under, and always the over, by market and by line band,
    scored against the box score, season by season.

A pattern that pays in every season on real samples is a market bias worth
publishing. One that pays on average but flips between seasons is the thing
that has already fooled this project twice, so seasons are reported separately
and the last one is the test rather than the evidence.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

# The market key a book posts, and the box score column it settles against.
MARKETS = {
    "player_receptions": "receptions",
    "player_reception_yds": "receiving_yards",
    "player_rush_yds": "rushing_yards",
    "player_rush_attempts": "carries",
    "player_pass_yds": "passing_yards",
    "player_pass_attempts": "attempts",
    "player_pass_completions": "completions",
}
BANDS = {
    "receptions": [(0, 2.5), (2.5, 4.5), (4.5, 99)],
    "receiving_yards": [(0, 25), (25, 40), (40, 60), (60, 999)],
    "rushing_yards": [(0, 25), (25, 50), (50, 999)],
    "carries": [(0, 6), (6, 12), (12, 99)],
    "passing_yards": [(0, 240), (240, 999)],
    "attempts": [(0, 999)],
    "completions": [(0, 999)],
}
MIN_PER_SEASON = 60


def load(engine) -> pd.DataFrame:
    """One row per prop per game: the line, both prices, and what happened."""
    rows = pd.read_sql(
        text(
            """
            WITH snap AS (
                -- One observation per prop per game, the earliest we hold. The
                -- earliest is the least likely to have been moved by the money
                -- we are trying to find a bias in.
                SELECT DISTINCT ON (s.provider_event_id, s.player_name, s.market_key,
                                    lower(s.outcome_name))
                       s.provider_event_id, s.player_name, s.market_key,
                       lower(s.outcome_name) AS side, s.line, s.price_american,
                       (s.commence_time AT TIME ZONE 'America/New_York')::date AS game_date
                FROM odds_snapshots s
                WHERE s.line IS NOT NULL AND s.price_american IS NOT NULL
                  AND s.market_key = ANY(:markets)
                  AND lower(s.outcome_name) IN ('over', 'under')
                ORDER BY s.provider_event_id, s.player_name, s.market_key,
                         lower(s.outcome_name), s.observed_at
            )
            SELECT o.player_name, o.market_key, o.game_date, o.line,
                   MAX(o.price_american) FILTER (WHERE o.side = 'over')  AS over_price,
                   MAX(o.price_american) FILTER (WHERE o.side = 'under') AS under_price
            FROM snap o
            GROUP BY o.player_name, o.market_key, o.game_date, o.line
            """
        ),
        engine,
        params={"markets": list(MARKETS)},
    )
    stats = pd.read_sql(
        text(
            """
            SELECT p.name AS player_name, g.game_date, g.season,
                   COALESCE(g.receptions, 0)      AS receptions,
                   COALESCE(g.receiving_yards, 0) AS receiving_yards,
                   COALESCE(g.rushing_yards, 0)   AS rushing_yards,
                   COALESCE(g.carries, 0)         AS carries,
                   COALESCE(g.passing_yards, 0)   AS passing_yards,
                   COALESCE(g.attempts, 0)        AS attempts,
                   COALESCE(g.completions, 0)     AS completions
            FROM player_game_stats_app g
            JOIN players p ON p.external_id = g.player_id
            WHERE g.season_type = 'REG'
            """
        ),
        engine,
    )

    def norm(s):
        return (s.str.lower().str.replace(".", "", regex=False)
                 .str.replace("-", " ", regex=False).str.split().str.join(" "))

    rows["key"] = norm(rows["player_name"])
    stats["key"] = norm(stats["player_name"])
    rows["game_date"] = pd.to_datetime(rows["game_date"])
    stats["game_date"] = pd.to_datetime(stats["game_date"])
    df = rows.merge(stats.drop(columns=["player_name"]), on=["key", "game_date"],
                    how="inner")
    df["stat"] = df["market_key"].map(MARKETS)
    df["actual"] = [r[c] for r, c in zip(df.to_dict("records"), df["stat"])]
    return df


def profit(hit, price):
    price = np.asarray(price, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        win = np.where(price > 0, price / 100.0, 100.0 / -price)
    return np.where(hit, win, -1.0)


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = load(eng)
    if df.empty:
        raise SystemExit("no priced props could be matched to box scores")
    df = df[df["actual"].notna() & (df["actual"] != df["line"])]
    print(f"{len(df)} priced props matched to box scores, "
          f"{df['season'].min()}-{df['season'].max()}\n")

    out = []
    for stat, bands in BANDS.items():
        d = df[df["stat"] == stat]
        for lo, hi in bands:
            b = d[(d["line"] >= lo) & (d["line"] < hi)]
            if b.empty:
                continue
            for side in ("under", "over"):
                hit = (b["actual"] < b["line"]) if side == "under" else (b["actual"] > b["line"])
                price = b[f"{side}_price"]
                ok = price.notna()
                if ok.sum() < MIN_PER_SEASON:
                    continue
                p = profit(hit[ok], price[ok])
                rec = {"stat": stat, "band": f"{lo:g}-{hi:g}", "side": side,
                       "n": int(ok.sum()), "won": float(hit[ok].mean()),
                       "roi": float(p.mean())}
                for season, g in b[ok].groupby("season"):
                    h = (g["actual"] < g["line"]) if side == "under" else (g["actual"] > g["line"])
                    if len(g) >= MIN_PER_SEASON:
                        rec[str(int(season))] = float(profit(h, g[f"{side}_price"]).mean())
                out.append(rec)

    res = pd.DataFrame(out)
    seasons = [c for c in res.columns if c.isdigit()]
    res["seasons_up"] = (res[seasons] > 0).sum(axis=1)
    res["seasons_seen"] = res[seasons].notna().sum(axis=1)
    res = res.sort_values("roi", ascending=False)

    print(f"{'market':<17}{'band':<10}{'side':<7}{'n':>6}{'won':>8}{'roi':>8}"
          f"{'up/seen':>9}   per season")
    for r in res.itertuples(index=False):
        per = "  ".join(f"{s}:{getattr(r, '_' + str(i + 6), float('nan')):+.0%}"
                        for i, s in enumerate(seasons)
                        if not pd.isna(getattr(r, "_" + str(i + 6), float("nan"))))
        print(f"{r.stat:<17}{r.band:<10}{r.side:<7}{r.n:>6}{r.won:>8.1%}{r.roi:>+8.1%}"
              f"{str(r.seasons_up) + '/' + str(r.seasons_seen):>9}   {per}")


if __name__ == "__main__":
    main()
