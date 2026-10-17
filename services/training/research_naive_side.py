"""Is the board's selection layer worth anything over "take the side we project"?

The alternate-line test did not test this model. It asked whether sportsbooks
misprice a floor, they do not, and no model was involved in the question. What
the model has actually been measured at is this:

    lift over the base rate, from research_alt_lines.py

    pass_td  at the line   47.9% -> 58.6%   +10.6%   n=198
    rush_att one below     58.1% -> 61.8%    +3.7%   n=785
    recs     at the line   48.0% -> 51.6%    +3.6%   n=1,009
    rec_yds  every threshold         +0.4% to -0.4%  n=3,031

So it discriminates in count markets and not in yardage, and the pass_td number
is large. 58.6% against a -110 break-even of 52.4% is +11.9% a bet. The live
board returns -8.8% on pass_td.

Both of those cannot be true of the same picks, which means the loss is not in
the projection, it is in everything bolted on top of it: a probability eleven
points low, an expected-value filter fed by that probability, and tiers built
from the filter. This compares the whole apparatus against the simplest rule
there is.

    naive:  over if our median is above the line, under if below. Nothing else.
    board:  what was actually published and settled.

Scored on the same props so the difference is the rule and not the sample. When
the naive side differs from the published one there is no stored price for it,
so those are counted at -110 and the whole comparison is repeated at a flat -110
on both sides, where no price assumption survives at all.

If the naive rule wins, the selection layer is destroying value and the repair
is to stop doing most of it. If it loses, the layer earns its place and the
projection is the problem. Either answer is worth having.

Read-only.

    docker compose run --rm training python research_naive_side.py
    docker compose run --rm -e SOURCE=live training python research_naive_side.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "")
SINCE = os.getenv("SINCE", "")
FLAT = -110.0
COUNT_MARKETS = ("pass_td", "recs", "rush_att", "pass_completions", "pass_att")
YARD_MARKETS = ("rec_yds", "rush_yds", "pass_yds")


def payout(american):
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, a / 100.0, 100.0 / np.abs(a))


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    where = ["hit IS NOT NULL", "actual IS NOT NULL", "line IS NOT NULL",
             "projection_median IS NOT NULL", "price_american IS NOT NULL"]
    params = {}
    if SOURCE:
        where.append("source = :source")
        params["source"] = SOURCE
    if SINCE:
        where.append("game_date >= :since")
        params["since"] = SINCE
    d = pd.read_sql(text("""
        SELECT market_code, game_date, source, edge_tier, recommended_side,
               line, actual, projection_median AS p50, hit, price_american
        FROM prop_edge_results
        WHERE """ + " AND ".join(where)), eng, params=params)
    if d.empty:
        raise SystemExit("no graded rows for that population")

    print(f"population: source={SOURCE or 'any'}  since={SINCE or 'all time'}")
    for src, n in d.groupby("source").size().items():
        print(f"  {str(src):<10}{n:>7} rows")
    if not SOURCE and d["source"].nunique() > 1:
        print("  WARNING: live and backtest rows are mixed. Pass SOURCE=live "
              "for the record.")
    print()

    over_hit = (d["actual"] > d["line"]).to_numpy()
    d["naive_side"] = np.where(d["p50"] > d["line"], "over", "under")
    d["naive_hit"] = np.where(d["naive_side"] == "over", over_hit, ~over_hit)
    d["flipped"] = d["naive_side"] != d["recommended_side"]
    d["cluster"] = d["game_date"].astype(str)

    pay_real = payout(d["price_american"])
    pay_flat = payout(np.full(len(d), FLAT))
    d["u_board"] = np.where(d["hit"].astype(bool), pay_real, -1.0)
    d["u_naive"] = np.where(d["naive_hit"],
                            np.where(d["flipped"], pay_flat, pay_real), -1.0)
    d["u_board_flat"] = np.where(d["hit"].astype(bool), pay_flat, -1.0)
    d["u_naive_flat"] = np.where(d["naive_hit"], pay_flat, -1.0)

    def row(sub, label, flat=False):
        if len(sub) < 40:
            return
        b = sub["u_board_flat" if flat else "u_board"].to_numpy()
        n_ = sub["u_naive_flat" if flat else "u_naive"].to_numpy()
        diff = n_ - b
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(diff[i])), sub["cluster"].to_numpy(),
            n_boot=2000)
        print(f"  {label:<20}{len(sub):>7}{sub['hit'].mean():>8.1%}"
              f"{sub['naive_hit'].mean():>9.1%}{sub['flipped'].mean():>9.1%}"
              f"{b.mean():>+9.1%}{n_.mean():>+9.1%}{r:>+9.1%}"
              f"  [{lo:>+6.1%},{hi:>+6.1%}]")

    for flat in (False, True):
        print("at a flat -110 on both sides" if flat
              else "at the prices actually taken (flips scored at -110)")
        print(f"  {'population':<20}{'n':>7}{'board':>8}{'naive':>9}"
              f"{'flipped':>9}{'ROI brd':>9}{'ROI nve':>9}{'change':>9}  95% CI")
        row(d, "everything", flat)
        row(d[d["market_code"].isin(COUNT_MARKETS)], "count markets", flat)
        row(d[d["market_code"].isin(YARD_MARKETS)], "yardage markets", flat)
        for m in sorted(d["market_code"].unique()):
            row(d[d["market_code"] == m], f"  {m}", flat)
        print()

    # The narrowest version of the claim: count markets, taking the over only
    # when our median clears the line. That is the cell the lift was measured
    # in, and it is the one worth knowing in isolation.
    print("the lift cell on its own: count markets, our median above the line")
    print(f"  {'market':<20}{'n':>7}{'hit':>8}{'ROI':>9}  95% CI")
    sel = d[(d["market_code"].isin(COUNT_MARKETS)) & (d["p50"] > d["line"])]
    for label, sub in [("all count markets", sel)] + [
            (f"  {m}", sel[sel["market_code"] == m])
            for m in sorted(sel["market_code"].unique())]:
        if len(sub) < 40:
            continue
        w = np.where(over_hit[sub.index], payout(sub["price_american"]), -1.0)
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w[i])), sub["cluster"].to_numpy(), n_boot=2000)
        print(f"  {label:<20}{len(sub):>7}{over_hit[sub.index].mean():>8.1%}"
              f"{r:>+9.1%}  [{lo:>+6.1%},{hi:>+6.1%}]")


    # What the prices themselves are costing.
    #
    # The naive rule and the board agreed on 100% of 1,296 live picks: the side
    # is already chosen by median against line and nothing downstream moves it.
    # So the selection layer is not destroying the signal by flipping, and the
    # difference between the two ROI columns above is not strategy at all, it is
    # the price. Weighted across markets it is 1.7 points, and on pass_td it is
    # 9.7: at a flat -110 that market returns +0.9% and at the prices actually
    # taken it returns -8.8%. The entire loss there is what we paid to get on.
    #
    # So the question is whether refusing short prices is worth more than the
    # bets it costs us. A price floor is one line in the edge builder if it is.
    print("ROI by the price taken, which is the lever the board does not pull")
    print(f"  {'minimum price':<16}{'n':>7}{'share':>8}{'hit':>8}{'ROI':>9}"
          f"  95% CI{'':<10}{'count mkts only':>18}")
    for floor in (None, -200, -150, -130, -120, -110, -105, 100):
        sub = d if floor is None else d[d["price_american"] >= floor]
        if len(sub) < 40:
            continue
        w = sub["u_board"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w[i])), sub["cluster"].to_numpy(), n_boot=2000)
        c = sub[sub["market_code"].isin(COUNT_MARKETS)]
        if len(c) >= 40:
            cw = c["u_board"].to_numpy()
            cr, _, _ = S.clustered_bootstrap(
                lambda i: float(np.mean(cw[i])), c["cluster"].to_numpy(), n_boot=1000)
            ctxt = f"{len(c):>6} {cr:>+9.1%}"
        else:
            ctxt = f"{len(c):>6} {'-':>9}"
        label = "everything" if floor is None else f"{floor:+d} or longer"
        print(f"  {label:<16}{len(sub):>7}{len(sub)/len(d):>8.0%}"
              f"{sub['hit'].mean():>8.1%}{r:>+9.1%}  [{lo:>+6.1%},{hi:>+6.1%}]"
              f"{ctxt:>18}")



if __name__ == "__main__":
    main()
