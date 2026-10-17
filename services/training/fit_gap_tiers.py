"""Tier on the gap, per market, so elite is the band that hits most.

The tiers the site advertises today carry no information. Live 2026, one row
per distinct prop:

    elite    242   50.0%
    strong   213   49.8%
    medium   265   46.8%
    small    576   51.6%

Small beats elite. That is not a threshold being slightly off, it is the tier
meaning nothing, and it happens because edge_tier() in build_prop_edges cuts on
the probability edge in percentage points rather than on the disagreement
between the projection and the line.

The gap does carry information, measured on the same picks:

    gap / line   0.00-0.10   260   48.8%
                 0.10-0.19   259   47.1%
                 0.19-0.31   259   48.3%
                 0.31-0.43   259   49.0%
                 0.43+       259   56.8%
    top minus bottom +7.9% [+1.7%, +21.2%]

As a share of the line, not in raw units: the same table on raw units is -0.2%
[-10.9%, +10.7%], flat. A two yard gap means something different on a 4.5 yard
line than on a 70 yard one, and only the ratio knows that.

Per market the top band is where the picks are:

    recs      0.40+   67.1%   top minus bottom +19.2% [+11.7%, +26.3%]
    rush_att  0.31+   66.7%                    +33.3% [ +5.6%, +59.4%]
    rec_yds   0.57+   52.2%                    +10.4% [ -6.2%, +31.2%]
    rush_yds  0.03-0.20 61.3%                  -16.1% [-36.3%,  +2.5%]

Two of those clear zero outright. rush_yds runs BACKWARDS, its smallest gaps
being its most accurate, which is a real finding and is handled rather than
averaged away: a market whose ordering does not hold on the fitting data is
reported and left on flat tiers rather than being given a confident label.

An earlier attempt ordered fine bands by their own accuracy and let a tier own
scattered ranges. On live rows that returned elite 52.9%, strong 48.9%, medium
48.2%, small 49.1% -- it had fitted the backtest's noise. So this does the
simple thing instead: quintiles of the gap, in order, elite at the top. The
thresholds come from the backtest and the accuracy is read on the live season,
which is the only number quoted.

Writes artifacts/gap_tiers.json with WRITE=1.

    docker compose run --rm training python fit_gap_tiers.py
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
OUT = ARTIFACTS / "gap_tiers.json"

MIN_FIT = int(os.getenv("MIN_FIT", "300"))
MIN_TEST_CELL = int(os.getenv("MIN_TEST_CELL", "20"))
# elite, strong, medium, then everything below is small.
CUTS = (0.80, 0.60, 0.40)
NAMES = ("elite", "strong", "medium", "small")


def load(eng, source):
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, line, recommended_side,
               hit, projection_median AS p50
        FROM prop_edge_results
        WHERE source = :s AND hit IS NOT NULL AND line IS NOT NULL
          AND projection_median IS NOT NULL
    """), eng, params={"s": source})
    if d.empty:
        return d
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line", "recommended_side"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["gap"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["cluster"] = d["game_date"].astype(str)
    return d


def tier_of(gap, thresholds):
    for name, t in zip(NAMES, thresholds):
        if gap >= t:
            return name
    return NAMES[-1]


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    fit, test = load(eng, "backtest"), load(eng, "live")
    if fit.empty or test.empty:
        raise SystemExit("need both backtest and live graded rows")

    print(f"thresholds from {len(fit):,} backtest props, "
          f"accuracy read on {len(test):,} live props")
    print(f"  {'market':<18}{'elite at':>10}{'strong':>9}{'medium':>9}"
          f"{'ordered on the fit':>21}")

    out = {}
    for m in sorted(set(fit["market_code"]) & set(test["market_code"])):
        gf = fit[fit["market_code"] == m]
        if len(gf) < MIN_FIT:
            print(f"  {m:<18}{'-':>10}{'-':>9}{'-':>9}"
                  f"{f'only {len(gf)} fit rows':>21}")
            continue
        thr = [float(gf["gap"].quantile(q)) for q in CUTS] + [-np.inf]
        gf = gf.assign(tier=[tier_of(g, thr) for g in gf["gap"]])
        acc = {t: gf.loc[gf["tier"] == t, "hit"].mean() for t in NAMES
               if (gf["tier"] == t).sum() >= 25}

        # Does elite beat the market's own average by more than luck?
        #
        # "elite > small" was the first version of this test and it is useless:
        # rehearsed on pure noise with 4,000 rows it passed, because one of two
        # numbers is larger about half the time. The gate is now the bootstrap
        # interval on elite minus the rest, and it has to clear zero. A market
        # whose gap does not rank it keeps flat tiers, which is an honest
        # "we do not know" rather than a confident label on a coin flip.
        el = gf.loc[gf["tier"] == "elite", "hit"].to_numpy()
        rest = gf.loc[gf["tier"] != "elite", "hit"].to_numpy()
        ordered = False
        margin = float("nan")
        if len(el) >= 25 and len(rest) >= 25:
            vals = np.concatenate([el, rest])
            flag = np.concatenate([np.ones(len(el)), np.zeros(len(rest))])
            cl = np.concatenate([
                gf.loc[gf["tier"] == "elite", "cluster"].to_numpy(),
                gf.loc[gf["tier"] != "elite", "cluster"].to_numpy()])
            margin, mlo, _ = S.clustered_bootstrap(
                lambda i: (float(np.mean(vals[i][flag[i] == 1]))
                           - float(np.mean(vals[i][flag[i] == 0]))
                           if (flag[i] == 1).any() and (flag[i] == 0).any()
                           else 0.0),
                cl, n_boot=1500)
            ordered = mlo > 0
        print(f"  {m:<18}{thr[0]:>10.3f}{thr[1]:>9.3f}{thr[2]:>9.3f}"
              f"{(f'yes, +{margin:.1%}' if ordered else 'NO, left flat'):>21}")
        if ordered:
            out[m] = {"elite": thr[0], "strong": thr[1], "medium": thr[2],
                      "fit_rows": int(len(gf)),
                      "fit_accuracy": {t: float(v) for t, v in acc.items()}}

    print()
    print("accuracy on the live season, which is the number that counts")
    print(f"  {'market':<18}{'tier':<9}{'picks':>7}{'correct':>9}  95% CI")
    for m in sorted(out):
        g = test[test["market_code"] == m]
        if g.empty:
            continue
        thr = [out[m]["elite"], out[m]["strong"], out[m]["medium"], -np.inf]
        g = g.assign(tier=[tier_of(x, thr) for x in g["gap"]])
        for t in NAMES:
            cell = g[g["tier"] == t]
            if len(cell) < MIN_TEST_CELL:
                print(f"  {m if t == NAMES[0] else '':<18}{t:<9}{len(cell):>7}"
                      f"{'-':>9}")
                continue
            w = cell["hit"].to_numpy()
            r, lo, hi = S.clustered_bootstrap(
                lambda i: float(np.mean(w[i])), cell["cluster"].to_numpy(),
                n_boot=2000)
            print(f"  {m if t == NAMES[0] else '':<18}{t:<9}{len(cell):>7}"
                  f"{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]")
        print()

    # Pooled, since the site shows one elite list across markets.
    keep = test[test["market_code"].isin(out)].copy()
    if not keep.empty:
        keep["tier"] = [
            tier_of(r.gap, [out[r.market_code]["elite"],
                            out[r.market_code]["strong"],
                            out[r.market_code]["medium"], -np.inf])
            for r in keep.itertuples(index=False)]
        print("all markets that kept a tiering, pooled:")
        print(f"  {'tier':<9}{'picks':>7}{'share':>8}{'correct':>9}  95% CI")
        for t in NAMES:
            cell = keep[keep["tier"] == t]
            if len(cell) < MIN_TEST_CELL:
                continue
            w = cell["hit"].to_numpy()
            r, lo, hi = S.clustered_bootstrap(
                lambda i: float(np.mean(w[i])), cell["cluster"].to_numpy(),
                n_boot=2000)
            print(f"  {t:<9}{len(cell):>7}{len(cell)/len(keep):>8.0%}"
                  f"{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]")

    if os.getenv("WRITE") == "1":
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {OUT.name} for {len(out)} market(s): {sorted(out)}")
    else:
        print("\nnothing written. WRITE=1 to save the thresholds.")


if __name__ == "__main__":
    main()
