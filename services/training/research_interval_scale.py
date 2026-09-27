"""Is the quantile ladder too narrow in a way that fixing would help us?

Across every market, only 37-41% of outcomes land inside the predicted middle
50%, where 50% is correct. The band is too narrow everywhere, so every z, which
is the gap divided by (q75 - q25) / 1.349, is inflated.

That sounds like a bug and might be nothing. z is used for one thing: ranking
picks and cutting tiers. If the ladder is too narrow by a constant factor then
every z is inflated by that same factor, the ordering is untouched, and widening
it would relabel the board without changing a single decision. The thresholds
were measured in these units, so they already absorb a constant.

It only helps if the error is uneven, so the three questions in order:

    1. How far off is the coverage, per market and per projection level? A
       constant miss is cosmetic. A miss that varies means z is measured in
       different units for different players, and a pick on a low-volume tight
       end is being compared against one on an alpha receiver using two
       different rulers.

    2. Does a corrected scale rank better? Compared at matched pick counts, not
       matched thresholds: widening the ladder shrinks every z, so holding the
       thresholds fixed would publish fewer picks and a stricter board would
       look better for reasons that have nothing to do with calibration. Taking
       the top N by |z| under each scale and comparing hit rate is the only
       comparison that isolates the ranking.

    3. Would it actually pay? Hit rate and return on the board it would build.

If question one says the miss is flat, the answer to the whole file is that the
published range should be widened for honesty, the number a reader sees is wrong
by a third, and none of it changes which picks we make. That is worth knowing and
worth saying rather than dressing a cosmetic change as an improvement.

Verdict, September 2026: real, and not worth acting on for the board.

The miss is flat. Every market needs its ladder widened by between 1.29 and
1.41, a spread of 0.12, and inside each market the coverage barely moves across
four levels of projection (rec_yds 39/39/40/38, rush_att 34/38/39/39). One ruler
fits every player; it is simply the wrong length, by about 35%.

So the ranking does not improve. At matched pick counts, which is the only
comparison that separates calibration from strictness:

    scale                         top 300      top 600     top 1200
    as shipped                     61.0%        57.2%        57.0%
    scaled per market              61.0%        57.3%        56.8%
    scaled per market and level    60.0%        58.0%        56.8%

Every cell inside every other cell's interval. Correcting the scale reorders
nothing, because a constant factor cannot.

The third table looks like an improvement and is not one:

    scale                        published     hit      roi
    as shipped                        1244   58.6%    +9.4%
    scaled per market                  699   60.2%   +12.4%
    scaled per market and level        674   60.5%   +13.4%

A wider ladder shrinks every z, so the same thresholds publish half as many
picks. That is a stricter board, not a better-calibrated one, and the same
result is available by raising the thresholds directly without touching the
ladder. Reading it as a calibration win would be the exact confound this file was
built to avoid.

What remains true is that the published range is wrong for a reader. A band
drawn as "20 to 108" should be nearer "12 to 120", and someone judging whether a
projection is confident is being shown a third more certainty than the model
has. That is a display fix in fit_interval_calibrator's territory, it does not
belong in gap_tier, and it must not be made without moving the thresholds by the
same factor, since they were measured in the old units and would otherwise
silently halve the board.
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S

import backtest_gap_system as bt
import gap_tier

N_BOOT = int(os.getenv("N_BOOT", "1500"))
# Half the middle-50% band, in standard deviations, for a normal.
IQR_HALF = 0.6745


def coverage(d, sd_col="sd"):
    inside = np.abs(d["label_actual"] - d["pred"]) <= IQR_HALF * d[sd_col]
    return float(inside.mean())


def main():
    print("Rebuilding walk-forward picks...\n")
    b = bt.build()
    b = b[np.isfinite(b["sd"]) & np.isfinite(b["z"])].copy()
    if len(b) < 1000:
        raise SystemExit("not enough rows")

    print("=" * 78)
    print("1. How far off is it, and is the miss flat?\n")
    print(f"  {'market':<12}{'rows':>7}{'covered':>10}{'needs x':>10}"
          f"   by projection level (low -> high)")
    factors = {}
    for code, g in b.groupby("market_code"):
        if len(g) < 200:
            continue
        cov = coverage(g)
        # The multiple that would put coverage at 50%: the ratio of the actual
        # median absolute error to what the current sd implies it should be.
        need = float(np.median(np.abs(g["label_actual"] - g["pred"]))
                     / (IQR_HALF * g["sd"].median()))
        factors[code] = need
        # Same thing in four slices of the projection, to see whether one ruler
        # fits every player.
        q = pd.qcut(g["pred"].rank(method="first"), 4, labels=False)
        per = []
        for i in range(4):
            s = g[q == i]
            if len(s) < 40:
                per.append("  -  ")
                continue
            per.append(f"{coverage(s):.0%}")
        print(f"  {code:<12}{len(g):>7}{cov:>9.1%}{need:>10.2f}   "
              + "  ".join(per))

    spread = max(factors.values()) - min(factors.values())
    print(f"\n  The needed multiple ranges {min(factors.values()):.2f} to "
          f"{max(factors.values()):.2f}, a spread of {spread:.2f}.")
    print("  A flat miss is cosmetic; a varying one changes the ranking.")

    print("\n" + "=" * 78)
    print("2. Does a corrected scale rank better, at matched pick counts?\n")
    # Per-market correction, and a per-market-and-level correction, so an uneven
    # miss inside a market is also tested.
    b["sd_market"] = b["sd"] * b["market_code"].map(factors).astype(float)
    lvl = b.groupby("market_code")["pred"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 4, labels=False))
    b["_lvl"] = lvl
    fine = {}
    for (code, i), g in b.groupby(["market_code", "_lvl"]):
        if len(g) < 60:
            fine[(code, i)] = factors.get(code, 1.0)
            continue
        fine[(code, i)] = float(np.median(np.abs(g["label_actual"] - g["pred"]))
                                / (IQR_HALF * g["sd"].median()))
    b["sd_fine"] = b["sd"] * [
        fine.get((c, i), 1.0) for c, i in zip(b["market_code"], b["_lvl"])]

    for name, col in (("as shipped", "sd"), ("scaled per market", "sd_market"),
                      ("scaled per market and level", "sd_fine")):
        b[f"z_{name}"] = (b["pred"] - b["line"]) / b[col]

    print(f"  {'scale':<30}{'top 300':>22}{'top 600':>22}{'top 1200':>22}")
    for name in ("as shipped", "scaled per market",
                 "scaled per market and level"):
        z = b[f"z_{name}"].abs()
        cells = []
        for n in (300, 600, 1200):
            s = b.loc[z.nlargest(n).index]
            w = s["won"].to_numpy()
            r, lo, hi = S.clustered_bootstrap(
                lambda i, w=w: float(np.mean(w[i])), s["cluster"].to_numpy(),
                n_boot=N_BOOT)
            cells.append(f"{r:>6.1%} [{lo:>5.1%},{hi:>5.1%}]")
        print(f"  {name:<30}" + "".join(f"{c:>22}" for c in cells))
    print("\n  Same picks counted, different ruler. A scale that ranks better "
          "wins here\n  regardless of where the thresholds sit.")

    print("\n" + "=" * 78)
    print("3. Would the board it builds actually pay?\n")
    print(f"  {'scale':<30}{'published':>11}{'hit':>22}{'roi':>9}")
    for name in ("as shipped", "scaled per market",
                 "scaled per market and level"):
        z = b[f"z_{name}"]
        tiers = [gap_tier.tier_for(v if np.isfinite(v) else None, c)
                 for v, c in zip(z, b["market_code"])]
        s = b[pd.Series(tiers, index=b.index).isin(["elite", "strong", "medium"])]
        if len(s) < 100:
            print(f"  {name:<30}{len(s):>11}   too few")
            continue
        w = s["won"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i, w=w: float(np.mean(w[i])), s["cluster"].to_numpy(),
            n_boot=N_BOOT)
        print(f"  {name:<30}{len(s):>11}   {r:>6.1%} [{lo:>5.1%},{hi:>5.1%}]"
              f"{s['profit'].mean():>+9.1%}")
    print("\n  Note the published counts: a wider ladder shrinks every z, so "
          "the same\n  thresholds publish fewer picks. Read this table with "
          "that in mind, and\n  read table 2 for whether the ranking itself "
          "improved.")


if __name__ == "__main__":
    main()
