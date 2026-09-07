"""Try to break the star-unders edge before trusting it with money.

The strategy showed +3.7% over break-even on 2025. That is exactly the kind of
result that turns out to be a measurement artefact, so this attacks it from
every angle I can think of:

  1. Does the MODEL add anything, or is the whole effect structural? If blind
     star-unders score the same as model-filtered ones, the model is decoration
     and I should say so rather than let it take credit.
  2. Is it stable week to week, or carried by a few slates? A real structural
     effect should show up in most weeks. An artefact lives in two of them.
  3. Bootstrap confidence interval on the edge, resampled by SLATE rather than
     by pick. Picks within a slate share game scripts and weather, so treating
     6,000 correlated picks as independent overstates significance badly.
  4. Is the "star" cut arbitrary? Top third, top quarter, top half, and an
     absolute line threshold should all point the same way if the mechanism is
     real.
  5. How much of it is best-price execution, and is that realistic with only
     three books?
  6. Sanity: do the flagged picks look like the intended population?
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

MARKET_STAT = {
    "player_reception_yds": "receiving_yards",
    "player_receptions": "receptions",
    "player_rush_yds": "rushing_yards",
    "player_rush_attempts": "carries",
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
}


def load(engine, years):
    yrs = ",".join(str(y) for y in years)
    case = "\n".join(
        f"WHEN c.market_key='{k}' THEN g.{v}" for k, v in MARKET_STAT.items()
    )
    return pd.read_sql(
        text(f"""
            WITH c AS (
              SELECT s.player_name, s.market_key,
                     (s.commence_time AT TIME ZONE 'UTC')::date AS game_date,
                     AVG(s.line) FILTER (WHERE lower(s.outcome_name)='over') AS line,
                     AVG(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                              ELSE 1+s.price_american/100.0 END)
                       FILTER (WHERE lower(s.outcome_name)='under') AS under_avg,
                     MAX(CASE WHEN s.price_american<0 THEN 1+100.0/(-s.price_american)
                              ELSE 1+s.price_american/100.0 END)
                       FILTER (WHERE lower(s.outcome_name)='under') AS under_best,
                     COUNT(DISTINCT s.bookmaker_key) AS books
              FROM odds_snapshots s
              WHERE EXTRACT(YEAR FROM s.commence_time) IN ({yrs})
              GROUP BY 1,2,3
            )
            SELECT c.*, p.external_id,
                   CASE {case} END::float8 AS actual
            FROM c
            JOIN players p
              ON lower(replace(replace(p.name,'.',''),'-',' ')) =
                 lower(replace(replace(c.player_name,'.',''),'-',' '))
            JOIN player_game_stats_app g
              ON g.player_id = p.external_id
             AND g.game_date BETWEEN c.game_date - 1 AND c.game_date + 1
            WHERE c.line IS NOT NULL
        """),
        engine,
    )


def prep(df):
    df = df.dropna(subset=["line", "actual"]).copy()
    df = df.drop_duplicates(subset=["player_name", "market_key", "game_date"])
    df = df[df["actual"] != df["line"]]
    df["under_hit"] = (df["actual"] < df["line"]).astype(float)
    df["tier"] = df.groupby("market_key")["line"].transform(
        lambda x: pd.qcut(x, 4, labels=False, duplicates="drop")
    )
    return df


def score(frame, price_col):
    if frame.empty:
        return None
    dec = frame[price_col].fillna(1.909)
    hit = frame["under_hit"].mean()
    be = (1.0 / dec).mean()
    units = np.where(frame["under_hit"] > 0, dec - 1.0, -1.0)
    return {"n": len(frame), "hit": hit, "be": be,
            "edge": hit - be, "roi": units.mean()}


def line(label, r):
    if r is None:
        print(f"  {label:<34} (empty)")
        return
    print(f"  {label:<34} n={r['n']:<6} hit={r['hit']:.3f} be={r['be']:.3f} "
          f"edge={r['edge']:+.3f} roi={r['roi']:+.3f}")


def bootstrap_by_slate(frame, price_col, n_boot=2000, seed=42):
    """Resample whole slates, not individual picks.

    Picks on the same slate share weather, game scripts and correlated outcomes.
    Treating them as independent shrinks the interval to something dishonest.
    """
    rng = np.random.default_rng(seed)
    slates = frame["game_date"].unique()
    by_slate = {d: g for d, g in frame.groupby("game_date")}
    edges = []
    for _ in range(n_boot):
        pick = rng.choice(slates, size=len(slates), replace=True)
        sample = pd.concat([by_slate[d] for d in pick], ignore_index=True)
        r = score(sample, price_col)
        if r:
            edges.append(r["edge"])
    return np.percentile(edges, [2.5, 50, 97.5])


def main():
    engine = create_engine(DATABASE_URL, future=True)

    d25 = prep(load(engine, [2025, 2026]))
    d34 = prep(load(engine, [2023, 2024]))
    top25 = d25[d25["tier"] == d25["tier"].max()]
    print(f"2025: {len(d25)} props over {d25['game_date'].nunique()} slates")
    print(f"2023/24: {len(d34)} props over {d34['game_date'].nunique()} slates")

    print("\n[1] DOES THE MODEL ADD ANYTHING?")
    print("    If blind == model-filtered, the edge is purely structural.")
    line("blind star unders (best price)", score(top25, "under_best"))
    line("blind star unders (avg price)", score(top25, "under_avg"))
    edges = pd.read_sql(text("""
        SELECT player_name, market_code, game_date, line, recommended_side
        FROM prop_edge_results
    """), engine)
    print("    (model-filtered comparison uses backtest_sides.py: 0.558 hit)")
    print("    blind star-under hit above is the honest baseline to beat.")

    print("\n[2] WEEK BY WEEK (is it carried by a few slates?)")
    wk = top25.groupby("game_date").apply(
        lambda g: pd.Series(score(g, "under_best")), include_groups=False
    )
    wk = wk[wk["n"] >= 20]
    print(f"    slates with >=20 picks: {len(wk)}")
    print(f"    slates with positive edge: {(wk['edge'] > 0).sum()}/{len(wk)}")
    print(f"    median slate edge: {wk['edge'].median():+.3f}")
    print(f"    worst / best slate: {wk['edge'].min():+.3f} / {wk['edge'].max():+.3f}")

    print("\n[3] BOOTSTRAP CI, RESAMPLED BY SLATE")
    lo, mid, hi = bootstrap_by_slate(top25, "under_best")
    print(f"    star unders best price: {mid:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    lo2, mid2, hi2 = bootstrap_by_slate(top25, "under_avg")
    print(f"    star unders avg price:  {mid2:+.3f}  95% CI [{lo2:+.3f}, {hi2:+.3f}]")
    print(f"    -> {'HOLDS' if lo > 0 else 'CI CROSSES ZERO, not significant'} at best price")

    print("\n[4] IS THE 'STAR' CUT ARBITRARY?")
    for label, frame in [
        ("top 25% by line", d25[d25["tier"] == 3]),
        ("top 50% by line", d25[d25["tier"] >= 2]),
        ("bottom 50% by line", d25[d25["tier"] <= 1]),
        ("bottom 25% by line", d25[d25["tier"] == 0]),
    ]:
        line(label, score(frame, "under_best"))

    print("\n[5] HOW MUCH IS EXECUTION vs STRUCTURE?")
    a = score(top25, "under_avg")
    b = score(top25, "under_best")
    if a and b:
        print(f"    structural (avg price):  {a['edge']:+.3f}")
        print(f"    with best price:         {b['edge']:+.3f}")
        print(f"    execution contributes:   {b['edge'] - a['edge']:+.3f}")
        print(f"    books quoted per prop:   {top25['books'].mean():.1f}")

    print("\n[6] INDEPENDENT SEASONS (no model, no overlap with discovery)")
    t34 = d34[d34["tier"] == d34["tier"].max()]
    line("2023/24 star unders (best)", score(t34, "under_best"))
    line("2023/24 star unders (avg)", score(t34, "under_avg"))
    lo3, mid3, hi3 = bootstrap_by_slate(t34, "under_best")
    print(f"    bootstrap: {mid3:+.3f}  95% CI [{lo3:+.3f}, {hi3:+.3f}]")

    print("\n[7] PER MARKET, star unders at best price (2025)")
    for m, g in top25.groupby("market_key"):
        line(m, score(g, "under_best"))


if __name__ == "__main__":
    main()
