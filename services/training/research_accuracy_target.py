"""Can each market reach 55% side accuracy, with what rule, and at what volume?

The goal is stated plainly: the projection should pick the correct side at least
55% of the time, in every market. Not ROI, not units. Accuracy.

Where that stands on the live season:

    pass_yds           53.2%   +1.8 to go
    pass_td            52.9%   +2.1
    recs               52.9%   +2.1
    rush_att           49.7%   +5.3
    rush_yds           49.7%   +5.3
    pass_att           48.8%   +6.2
    pass_completions   47.6%   +7.4
    rec_yds            46.9%   +8.1

Two distinct ways to close that, and only one of them is available this week.

Making the projection better is the hard one, and this project has already
measured that the volume markets sit at 91-127% of the R-squared the outcome
noise even allows, so there is not much left in the number itself.

The other is selection WITHIN each market. Every market keeps publishing, but
the picks it publishes are the ones a confidence statistic says it is right
about. If some statistic sorts a market's props so the top slice hits 55%, that
market reaches the target without a single change to the model. If no statistic
sorts it at all, the number is noise around a coin flip and no threshold rescues
it, which is also worth knowing and is a different repair.

So this takes every candidate confidence statistic and, per market, reports the
hit rate by decile of it. Then, for each market and each statistic, the most
selective threshold that still clears 55% and how many picks survive it.

Deduplicated to one row per distinct prop first. build_prop_edges publishes one
row per bookmaker, so the same prop is graded up to three times, and a hit rate
computed over the raw rows counts the same right or wrong answer repeatedly and
reports an interval narrower than the evidence allows.

Read-only.

    docker compose run --rm -e SOURCE=live training python research_accuracy_target.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "live")
SINCE = os.getenv("SINCE", "")
TARGET = float(os.getenv("TARGET", "0.55"))
MIN_KEEP = int(os.getenv("MIN_KEEP", "30"))


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    where = ["hit IS NOT NULL", "line IS NOT NULL", "projection_median IS NOT NULL"]
    params = {}
    if SOURCE:
        where.append("source = :source")
        params["source"] = SOURCE
    if SINCE:
        where.append("game_date >= :since")
        params["since"] = SINCE
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, line, recommended_side,
               edge_tier, hit, win_prob, expected_value,
               projection, projection_median AS p50, stddev
        FROM prop_edge_results
        WHERE """ + " AND ".join(where)), eng, params=params)
    if d.empty:
        raise SystemExit("no graded rows for that population")

    rows_before = len(d)
    d = d.sort_values("win_prob", ascending=False).drop_duplicates(
        subset=["game_date", "player_name", "market_code", "line",
                "recommended_side"])
    d["cluster"] = d["game_date"].astype(str)
    d["hit"] = d["hit"].astype(float)

    print(f"population: source={SOURCE or 'any'}  since={SINCE or 'all time'}")
    print(f"  {rows_before:,} graded rows -> {len(d):,} distinct props "
          f"({rows_before / max(len(d), 1):.2f} rows per prop)")
    print(f"  target {TARGET:.0%} side accuracy, minimum {MIN_KEEP} picks to count")
    print()

    # Candidate confidence statistics. Each is "how sure are we", so larger is
    # meant to mean more accurate, and the whole question is whether any of them
    # actually does.
    d["gap_rel"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["gap_sd"] = (d["p50"] - d["line"]).abs() / d["stddev"].abs().clip(lower=0.1)
    d["gap_pt"] = (d["projection"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["agree"] = np.where(
        np.sign(d["projection"] - d["line"]) == np.sign(d["p50"] - d["line"]),
        d["gap_rel"], 0.0)
    STATS = [("win_prob", "published probability"),
             ("expected_value", "expected value"),
             ("gap_rel", "gap to line, relative"),
             ("gap_sd", "gap to line, in sd"),
             ("gap_pt", "gap from the point projection"),
             ("agree", "gap, when point and median agree")]

    overall = d.groupby("market_code")["hit"].agg(["size", "mean"])
    print(f"  {'market':<20}{'props':>7}{'now':>8}   hit rate by decile of each statistic")
    for market in sorted(d["market_code"].unique()):
        g = d[d["market_code"] == market]
        if len(g) < 60:
            print(f"  {market:<20}{len(g):>7}{g['hit'].mean():>8.1%}   "
                  f"too few distinct props to split")
            continue
        print(f"  {market:<20}{len(g):>7}{g['hit'].mean():>8.1%}")
        for col, label in STATS:
            v = g[col].replace([np.inf, -np.inf], np.nan)
            if v.notna().sum() < 60 or v.nunique() < 10:
                continue
            q = pd.qcut(v.rank(method="first"), 5, labels=False)
            cells = [g.loc[q == i, "hit"].mean() for i in range(5)]
            print(f"      {label:<34}" + "".join(f"{c:>8.1%}" for c in cells))
        print()

    # The decision version: per market and statistic, the most selective cut
    # that still clears the target, and what it costs in volume.
    print(f"  the most selective cut that still clears {TARGET:.0%}, per market")
    print(f"  {'market':<20}{'statistic':<34}{'kept':>7}{'share':>8}"
          f"{'hit':>8}  95% CI")
    any_found = False
    for market in sorted(d["market_code"].unique()):
        g = d[d["market_code"] == market]
        best = None
        for col, label in STATS:
            v = g[col].replace([np.inf, -np.inf], np.nan)
            if v.notna().sum() < MIN_KEEP:
                continue
            for pct in np.arange(0.0, 0.95, 0.05):
                thr = v.quantile(pct)
                sub = g[v >= thr]
                if len(sub) < MIN_KEEP:
                    continue
                h = float(sub["hit"].mean())
                if h >= TARGET and (best is None or len(sub) > best[2]):
                    best = (label, thr, len(sub), h)
        if best is None:
            print(f"  {market:<20}{'nothing reaches it':<34}{'-':>7}{'-':>8}"
                  f"{g['hit'].mean():>8.1%}")
            continue
        any_found = True
        label, thr, n, h = best
        sub_hit = g[g[dict((l, c) for c, l in STATS)[label]] >= thr]["hit"]
        w = sub_hit.to_numpy()
        cl = g.loc[sub_hit.index, "cluster"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w[i])), cl, n_boot=2000)
        print(f"  {market:<20}{label:<34}{n:>7}{n / len(g):>8.0%}"
              f"{h:>8.1%}  [{lo:>5.1%},{hi:>5.1%}]")
    if any_found:
        print()
        print("  A cut found by searching every threshold on the same data it is")
        print("  judged on is optimistic by construction. The interval beside it")
        print("  is the honest read, and anything whose lower bound sits under")
        print("  50% has not shown anything. Whatever survives that needs")
        print("  confirming on a week it has not seen before it goes near the board.")


if __name__ == "__main__":
    main()
