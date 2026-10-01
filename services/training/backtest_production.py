"""Backtest the system production actually runs, not a simplified stand-in.

backtest_gap_system.py measures the gap as

    z = (point projection - line) / the standard deviation of the player's
                                     recent games

and every threshold in gap_tier.py was read off that. build_prop_edges.py
computes

    z = (point projection - line) / ((q75 - q25) / 1.349)

where the quartiles come from the fitted quantile ensemble, after the PIT
calibration and the interval calibrator. Those two denominators are not the
same number. Over the three graded weeks of 2026 the quantile spread ran 1.4 to
1.9 times the window spread:

    market      window sd   quantile sd
    rec_yds         18.11         26.32
    rush_yds        17.70         25.26
    rush_att         2.64          4.93
    recs             1.30          1.71

So a disagreement worth z = 1.5 in the units the thresholds were measured in
arrives at the tier ladder as z = 0.8, and the board publishes a different set
of picks from the one that was validated. The numerator had the same problem
and was fixed; this is the other half of it.

This file closes it by fitting the quantile ensemble walk-forward too, so the
backtest and the board compute the same statistic from the same objects. Then
it re-derives the thresholds in those units rather than inheriting numbers
measured in different ones.

    for each market, for each target season
        train the live point family on every labelled row before that season
        train the five quantile models on the same rows, same feature space
        fit the PIT calibration on those rows
        predict the season, calibrate, read the ladder
        z = (projection - line) / ((q75 - q25) / 1.349)

Nothing here sees its own season. Production retrains weekly as the season
goes, so these numbers understate the live board, and that bias is worth
remembering when reading them.

It is the slow one in this directory: five quantile models per market per
season, about 25 minutes for the four markets with enough priced volume.

    python backtest_production.py              fit, grade, print, cache
    REUSE=1 python backtest_production.py      re-read the cache and reprint
"""

from __future__ import annotations

import os
import pathlib

import gap_tier
import numpy as np
import pandas as pd
import stats_ci as S
import train_quantiles as tq

import backtest_gap_system as bt
import build_prop_edges as bp

os.environ.setdefault("MARKET_CODE", "rec_yds")
os.environ.setdefault("LOOKBACK", "5")

import train as tr  # noqa: E402

SEASONS = bt.SEASONS
MARKETS = ["rec_yds", "recs", "rush_yds", "rush_att"]
QUANTILES = tq.QUANTILES
N_BOOT = int(os.getenv("N_BOOT", "2000"))
CACHE = pathlib.Path(os.getenv("CACHE_DIR", "/tmp")) / "backtest_production.pkl"
FAMILY = {"rec_yds": "vote_v1", "recs": "enet_v2",
          "rush_yds": "vote_v1", "rush_att": "vote_v1"}


def walk_forward(d: pd.DataFrame, family: str) -> pd.DataFrame:
    """Point projection and a calibrated quantile ladder, out of sample."""
    d = d.copy()
    d["pred"] = np.nan
    for q in QUANTILES:
        d[f"q{int(q * 100)}"] = np.nan

    for s in SEASONS:
        past = d[d["season"] < s]
        now = d.index[d["season"] == s]
        if len(past) < 2000 or not len(now):
            continue
        X_tr, cols = tr._build_feature_dataframe(past.copy())
        X_te, _ = tr._build_feature_dataframe(d.loc[now].copy())
        for c in cols:
            if c not in X_te.columns:
                X_te[c] = 0.0
        y_tr = past["label_actual"].astype(float).to_numpy()

        try:
            point = tr.build_model(family)
            point.fit(X_tr[cols], y_tr)
            d.loc[now, "pred"] = np.maximum(0.0, point.predict(X_te[cols]))
        except Exception as e:
            print(f"    {s} point model failed: {type(e).__name__}: {e}")
            continue

        # The ladder, in the point model's feature space, as train_quantiles
        # insists on. Fit on the same rows, so the calibration below is fitted
        # in sample for the calibration and out of sample for the season, which
        # is what production does too.
        try:
            tr_pred, te_pred = {}, {}
            for q in QUANTILES:
                m = tq.build_quantile_model(q)
                m.fit(X_tr[cols], y_tr)
                tr_pred[q] = np.maximum(0.0, m.predict(X_tr[cols]))
                te_pred[q] = np.maximum(0.0, m.predict(X_te[cols]))
            cal = tq.fit_calibration(tr_pred, y_tr)
            # Through build_prop_edges' own function rather than a reimplemented
            # one. It sorts each row's values before interpolating, which
            # matters on the rows where the independently fitted quantiles come
            # back out of order, and those are exactly the rows with the widest
            # spreads and therefore the smallest z. A backtest that smooths over
            # them is measuring a kinder system than the one that ships.
            cal_rows = [bp.calibrated_quantiles(
                {q: float(te_pred[q][i]) for q in QUANTILES}, cal)
                for i in range(len(now))]
            for q in QUANTILES:
                d.loc[now, f"q{int(q * 100)}"] = [r[q] for r in cal_rows]
        except Exception as e:
            print(f"    {s} quantile ladder failed: {type(e).__name__}: {e}")
    return d


def build() -> pd.DataFrame:
    out = []
    for code in MARKETS:
        d = bt.features(code)
        if d is None or len(d) < 3000:
            print(f"  {code}: too few labelled rows")
            continue
        fam = FAMILY.get(code, "vote_v1")
        print(f"  {code}: fitting point model and ladder, family {fam} ...")
        d = walk_forward(d, fam)
        d = d[d["pred"].notna() & d["q50"].notna()]
        lines = bt.lines(code)
        if lines.empty:
            print(f"  {code}: no priced lines")
            continue
        d = d.rename(columns={"name": "player_name"})
        d["d"] = pd.to_datetime(d["as_of_game_date"])
        j = d.merge(lines, on=["player_name", "d"], how="inner")
        if j.empty:
            continue
        j["market_code"] = code

        # Exactly what build_prop_edges computes.
        j["sd_quant"] = [gap_tier.sd_from_quantiles(a, b, code)
                         for a, b in zip(j["q25"], j["q75"])]
        wsd = pd.to_numeric(j.get("stddev"), errors="coerce")
        floor = gap_tier.MIN_SD.get(code)
        j["sd_window"] = wsd.where(wsd >= floor) if floor else wsd
        j["z"] = ((j["pred"] - j["line"]) / j["sd_quant"]).clip(
            -gap_tier.Z_CEILING, gap_tier.Z_CEILING)
        j["z_window"] = ((j["pred"] - j["line"]) / j["sd_window"]).clip(
            -gap_tier.Z_CEILING, gap_tier.Z_CEILING)
        j["z_median"] = ((j["q50"] - j["line"]) / j["sd_quant"]).clip(
            -gap_tier.Z_CEILING, gap_tier.Z_CEILING)
        j["z_median_window"] = ((j["q50"] - j["line"]) / j["sd_window"]).clip(
            -gap_tier.Z_CEILING, gap_tier.Z_CEILING)
        print(f"  {code}: {len(j)} priced props with a walk-forward ladder")
        out.append(j)

    b = pd.concat(out, ignore_index=True)
    b["cluster"] = (b["d"].dt.strftime("%Y-%m-%d") + "|"
                    + b["team"].fillna("?").astype(str))
    return b


def graded(b: pd.DataFrame, zcol: str) -> pd.DataFrame:
    g = b[np.isfinite(b[zcol])].copy()
    g["side"] = np.where(g[zcol] > 0, "over", "under")
    g["price"] = np.where(g["side"] == "over", g["over"], g["under"])
    g = g[g["price"].notna()].copy()
    push = g["label_actual"] == g["line"]
    g["won"] = np.where(g["side"] == "over",
                        g["label_actual"] > g["line"],
                        g["label_actual"] < g["line"])
    dec = np.where(g["price"] > 0, 1 + g["price"] / 100.0,
                   1 + 100.0 / np.abs(g["price"]))
    g["profit"] = np.where(push, 0.0, np.where(g["won"], dec - 1, -1.0))
    g["az"] = g[zcol].abs()
    return g


def ci(g):
    w = g["won"].to_numpy()
    if len(g) < 30:
        return float(w.mean()), None, None
    return S.clustered_bootstrap(lambda i, w=w: float(np.mean(w[i])),
                                 g["cluster"].to_numpy(), n_boot=N_BOOT)


def main():
    if os.getenv("REUSE") == "1" and CACHE.exists():
        b = pd.read_pickle(CACHE)
        print(f"reusing {len(b):,} cached rows from {CACHE}")
        # A cache written before a z column existed still holds everything the
        # column is made of, so derive rather than refit.
        for col, num, den in (("z", "pred", "sd_quant"),
                              ("z_window", "pred", "sd_window"),
                              ("z_median", "q50", "sd_quant"),
                              ("z_median_window", "q50", "sd_window")):
            if col not in b.columns:
                b[col] = ((b[num] - b["line"]) / b[den]).clip(
                    -gap_tier.Z_CEILING, gap_tier.Z_CEILING)
    else:
        b = build()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        b.to_pickle(CACHE)
        print(f"\ncached {len(b):,} rows to {CACHE}")

    print("\n" + "=" * 96)
    print("1. THE THREE STATISTICS, SAME ROWS\n")
    print(f"  {'statistic':<34}{'n':>7}{'side accuracy':>15}"
          f"{'overs called':>14}{'mean |z|':>10}")
    for col, label in (("z", "projection / quantile spread  (live)"),
                       ("z_window", "projection / window spread  (measured)"),
                       ("z_median", "median / quantile spread  (was live)"),
                       ("z_median_window", "median / window spread")):
        g = graded(b, col)
        print(f"  {label:<34}{len(g):>7}{g.won.mean():>15.1%}"
              f"{(g.side == 'over').mean():>14.0%}{g.az.mean():>10.2f}")
    truth = graded(b, "z")
    print(f"  {'the outcome':<34}{len(truth):>7}{'':>15}"
          f"{(truth.label_actual > truth.line).mean():>14.0%}")
    print("\n  A denominator cannot change which side a pick takes, only how big")
    print("  the disagreement looks. Side accuracy is identical for the first")
    print("  two by construction; what differs is where the thresholds land.\n")

    print("=" * 96)
    print("2. WHAT THE SHIPPED THRESHOLDS ACTUALLY PUBLISH\n")
    print(f"  {'statistic':<34}{'picks':>7}{'hit':>9}  {'95% CI':<18}"
          f"{'ROI':>9}{'units':>9}")
    for col, label in (("z", "projection / quantile spread"),
                       ("z_window", "projection / window spread"),
                       ("z_median", "median / quantile spread"),
                       ("z_median_window", "median / window spread")):
        g = graded(b, col)
        g["tier"] = [gap_tier.tier_for(v, c)
                     for v, c in zip(g[col], g["market_code"])]
        pub = g[g["tier"].isin(["elite", "strong", "medium"])]
        r, lo, hi = ci(pub)
        band = f"[{lo:>5.1%},{hi:>6.1%}]" if lo is not None else ""
        print(f"  {label:<34}{len(pub):>7}{r:>9.1%}  {band:<18}"
              f"{pub.profit.mean():>+9.1%}{pub.profit.sum():>+9.2f}")

    print("\n" + "=" * 96)
    print("3. THRESHOLDS RE-DERIVED IN THE UNITS THE BOARD COMPUTES\n")
    zc = os.getenv("Z_COL", "z_window")
    print(f"  (in the units of {zc})\n")
    g = graded(b, zc)
    for side in ("under", "over"):
        s = g[g["side"] == side]
        print(f"  {side}s, {len(s)} priced props\n")
        print(f"    {'|z| at least':>14}{'picks':>8}{'hit':>9}  {'95% CI':<18}"
              f"{'ROI':>9}{'units':>9}")
        for cut in (0.25, 0.4, 0.5, 0.6, 0.75, 1.0, 1.25, 1.5, 2.0):
            k = s[s["az"] >= cut]
            if len(k) < 40:
                continue
            r, lo, hi = ci(k)
            band = f"[{lo:>5.1%},{hi:>6.1%}]" if lo is not None else ""
            print(f"    {cut:>14.2f}{len(k):>8}{r:>9.1%}  {band:<18}"
                  f"{k.profit.mean():>+9.1%}{k.profit.sum():>+9.2f}")
        print()

    print("=" * 96)
    print("4. PRECISION AGAINST COVERAGE, WHICH IS THE ONLY TRADEOFF\n")
    n = len(g)
    print(f"  {'coverage':>10}{'picks':>8}{'|z| at':>9}{'hit':>9}"
          f"{'vs 52.4%':>11}{'ROI':>9}{'units':>9}{'overs':>8}")
    for frac in (1.0, .50, .35, .25, .15, .10, .07, .05, .03, .02):
        k = int(n * frac)
        s = g.nlargest(k, "az")
        print(f"  {frac:>9.0%}{k:>8}{s.az.min():>9.2f}{s.won.mean():>9.1%}"
              f"{s.won.mean() - 0.524:>+11.1%}{s.profit.mean():>+9.1%}"
              f"{s.profit.sum():>+9.2f}{(s.side == 'over').mean():>8.0%}")

    print("\n" + "=" * 96)
    print("5. PER MARKET, AT THE SHIPPED THRESHOLDS\n")
    g["tier"] = [gap_tier.tier_for(v, c) for v, c in zip(g[zc], g["market_code"])]
    pub = g[g["tier"].isin(["elite", "strong", "medium"])]
    print(f"  {'market':<12}{'picks':>7}{'hit':>9}  {'95% CI':<18}{'ROI':>9}"
          f"{'units':>9}{'overs':>8}")
    for c, s in pub.groupby("market_code"):
        r, lo, hi = ci(s)
        band = f"[{lo:>5.1%},{hi:>6.1%}]" if lo is not None else ""
        print(f"  {c:<12}{len(s):>7}{r:>9.1%}  {band:<18}"
              f"{s.profit.mean():>+9.1%}{s.profit.sum():>+9.2f}"
              f"{(s.side == 'over').mean():>8.0%}")
    print(f"\n  {'by tier':<12}")
    for t in ("elite", "strong", "medium"):
        s = pub[pub["tier"] == t]
        if not len(s):
            continue
        r, lo, hi = ci(s)
        band = f"[{lo:>5.1%},{hi:>6.1%}]" if lo is not None else ""
        print(f"  {t:<12}{len(s):>7}{r:>9.1%}  {band:<18}"
              f"{s.profit.mean():>+9.1%}{s.profit.sum():>+9.2f}"
              f"{(s.side == 'over').mean():>8.0%}")

    print("\n" + "=" * 96)
    print("6. IS THE LADDER CALIBRATED?\n")
    print("  Share of outcomes below each walk-forward quantile.\n")
    print(f"  {'market':<12}{'n':>7}" + "".join(
        f"{f'<q{int(q * 100)}':>9}" for q in QUANTILES))
    print(f"  {'target':<12}{'':>7}" + "".join(
        f"{q:>9.0%}" for q in QUANTILES))
    for c, s in b.groupby("market_code"):
        print(f"  {c:<12}{len(s):>7}" + "".join(
            f"{(s.label_actual < s[f'q{int(q * 100)}']).mean():>9.0%}"
            for q in QUANTILES))
    print(f"  {'ALL':<12}{len(b):>7}" + "".join(
        f"{(b.label_actual < b[f'q{int(q * 100)}']).mean():>9.0%}"
        for q in QUANTILES))

    print("\n" + "=" * 96)
    print("7. WHAT IF THE LADDER WERE SHIFTED ONTO THE POINT PROJECTION\n")
    print("  The site shows p50 as 'ours'. The board now picks sides from the")
    print("  point projection. When those two disagree by a fifth of a receiving")
    print("  yard total, the page contradicts the pick beside it. Shifting the")
    print("  whole ladder by (projection - q50) makes them one number and leaves")
    print("  the width, which is the per-player part worth having, alone.\n")
    shift = b["pred"] - b["q50"]
    print(f"  mean shift {shift.mean():+.2f}, median {shift.median():+.2f}, "
          f"as a share of the median {abs(shift / b['q50'].replace(0, np.nan)).median():.0%}")
    print(f"\n  {'market':<12}{'n':>7}" + "".join(
        f"{f'<q{int(q * 100)}':>9}" for q in QUANTILES) + "   after the shift")
    print(f"  {'target':<12}{'':>7}" + "".join(f"{q:>9.0%}" for q in QUANTILES))
    for c, s in b.groupby("market_code"):
        sh = s["pred"] - s["q50"]
        print(f"  {c:<12}{len(s):>7}" + "".join(
            f"{(s.label_actual < s[f'q{int(q * 100)}'] + sh).mean():>9.0%}"
            for q in QUANTILES))
    print(f"  {'ALL':<12}{len(b):>7}" + "".join(
        f"{(b.label_actual < b[f'q{int(q * 100)}'] + shift).mean():>9.0%}"
        for q in QUANTILES))
    print("\n  A shift cannot change which side a pick takes, because the board")
    print("  already measures the gap from the projection. What it changes is the")
    print("  number on the page and the probability read off the ladder.")


if __name__ == "__main__":
    main()
