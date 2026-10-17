"""One bet at the best price, instead of the same bet counted once per book.

Two things came out of the price work and this measures both.

The price floor is dead. Restricting to longer prices lowers the hit rate
monotonically, 50.0% at every price down to 41.0% above even money, because a
longer price is longer precisely when the bet is less likely. And the "flat
-110" column that suggested there was margin in the price was not a lever at
all: you cannot bet a -150 prop at -110, so that comparison described a market
that does not exist.

The lever that does exist is the one the board already half-knows about.
build_prop_edges publishes one row per bookmaker, so a single prop appears up to
three times at three prices, and prop_edge_results grades all three. Only the
best-bet flag ranks on price, and only for unders in the top two tiers. Every
other published pick keeps whichever book happened to be there.

That has two consequences and this reports them separately:

  1. The record is inflated. If one prop is three rows, then "1,296 graded
     props" is not 1,296 independent bets, and every sample size and interval
     computed off it is narrower than the data deserves.

  2. There is money in it. A bettor with three books open takes the best of the
     three. The board does not, and the difference across every market is a
     straight gain with no model change behind it.

So: collapse to one row per distinct prop, price it at the best, the worst and
the average available, and report all three against the record as graded. The
gap between best and average is what line shopping is worth here, and the gap
between the row count and the prop count is how much of the record was double
counted.

Read-only.

    docker compose run --rm -e SOURCE=live training python research_line_shopping.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "live")
SINCE = os.getenv("SINCE", "")


def payout(american):
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, a / 100.0, 100.0 / np.abs(a))


def roi(price, hit, clusters, label, n_boot=2000):
    u = np.where(np.asarray(hit, dtype=bool), payout(price), -1.0)
    r, lo, hi = S.clustered_bootstrap(
        lambda i: float(np.mean(u[i])), np.asarray(clusters), n_boot=n_boot)
    return label, len(u), float(np.mean(hit)), r, lo, hi, float(u.sum())


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    where = ["hit IS NOT NULL", "price_american IS NOT NULL", "line IS NOT NULL"]
    params = {}
    if SOURCE:
        where.append("source = :source")
        params["source"] = SOURCE
    if SINCE:
        where.append("game_date >= :since")
        params["since"] = SINCE
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, line, recommended_side,
               edge_tier, hit, price_american, bookmaker_key
        FROM prop_edge_results
        WHERE """ + " AND ".join(where)), eng, params=params)
    if d.empty:
        raise SystemExit("no graded rows for that population")

    d["cluster"] = d["game_date"].astype(str)
    key = ["game_date", "player_name", "market_code", "line", "recommended_side"]

    books = d["bookmaker_key"].nunique() if "bookmaker_key" in d else 0
    g = d.groupby(key, dropna=False)
    props = g.ngroups
    print(f"population: source={SOURCE or 'any'}  since={SINCE or 'all time'}")
    print(f"  {len(d):,} graded rows")
    print(f"  {props:,} distinct props (same player, market, line and side)")
    print(f"  {len(d) / props:.2f} rows per prop, across {books} bookmaker(s)")
    if len(d) / props > 1.05:
        print(f"  So the record counts the average prop {len(d)/props:.2f} times. Every")
        print(f"  interval computed from the row count is narrower than {props:,}")
        print("  independent bets justify.")
    print()

    # One row per prop, priced three ways.
    agg = g.agg(hit=("hit", "first"),
                best=("price_american", "max"),
                worst=("price_american", "min"),
                n_books=("price_american", "size")).reset_index()
    # Average payout across the books that offered it, which is what taking a
    # book at random comes to.
    avg_pay = g["price_american"].apply(lambda s: float(np.mean(payout(s)))).values
    agg["cluster"] = agg["game_date"].astype(str)

    print(f"  {'pricing':<28}{'n':>7}{'hit':>8}{'ROI':>9}{'units':>9}  95% CI")
    out = [
        roi(d["price_american"], d["hit"], d["cluster"], "as graded, every row"),
    ]
    for label, price in (("one bet, worst book", agg["worst"]),
                         ("one bet, best book", agg["best"])):
        out.append(roi(price, agg["hit"], agg["cluster"], label))
    # The average-price case needs the payout directly rather than a price.
    u = np.where(agg["hit"].astype(bool), avg_pay, -1.0)
    r, lo, hi = S.clustered_bootstrap(
        lambda i: float(np.mean(u[i])), agg["cluster"].to_numpy(), n_boot=2000)
    out.insert(2, ("one bet, average book", len(u), float(agg["hit"].mean()),
                   r, lo, hi, float(u.sum())))
    for label, n, hit, r, lo, hi, units in out:
        print(f"  {label:<28}{n:>7}{hit:>8.1%}{r:>+9.1%}{units:>+9.1f}"
              f"  [{lo:>+6.1%},{hi:>+6.1%}]")

    best = [o for o in out if o[0] == "one bet, best book"][0]
    avg = [o for o in out if o[0] == "one bet, average book"][0]
    print(f"\n  line shopping is worth {best[3] - avg[3]:+.1%} "
          f"({best[6] - avg[6]:+.1f} units over {best[1]:,} bets)")

    # Per market, because it has to work everywhere, not on average.
    print()
    print(f"  {'market':<20}{'props':>7}{'books':>7}{'avg book':>10}"
          f"{'best book':>11}{'gain':>8}")
    for m in sorted(agg["market_code"].unique()):
        sub = agg[agg["market_code"] == m]
        if len(sub) < 30:
            continue
        idx = sub.index.to_numpy()
        ua = np.where(sub["hit"].astype(bool), avg_pay[idx], -1.0)
        ub = np.where(sub["hit"].astype(bool), payout(sub["best"]), -1.0)
        print(f"  {m:<20}{len(sub):>7}{sub['n_books'].mean():>7.1f}"
              f"{ua.mean():>+10.1%}{ub.mean():>+11.1%}"
              f"{ub.mean() - ua.mean():>+8.1%}")


if __name__ == "__main__":
    main()
