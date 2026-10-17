"""Does a bigger disagreement with the line mean a more accurate pick?

That is the whole product in one question. The model projects, the line comes
in, we take a side, and the tier says how far apart the two numbers were. Elite
is meant to be "we disagree a lot and we are usually right". If accuracy does
not rise with the size of the disagreement, the tiers are decoration.

No prices in here. No expected value, no units, no ROI. One column that matters:
of the picks in this band, what share were on the correct side.

The board does not currently tier on the gap at all. edge_tier() in
build_prop_edges cuts on the probability edge in percentage points: 15 or more
is elite, 10 strong, 5 medium, 2 small. Gap tiering exists in gap_tier.py and is
switched off, because the last head-to-head between them was scored in UNITS and
the gap version lost. Units are not the question being asked now, and a rule can
easily be more accurate while returning less, since a large disagreement tends
to come with a longer price. So this re-runs that comparison on accuracy alone.

One warning worth having in front of you before reading the output. This
project has already measured, on both halves of a season, that accuracy peaks
at a gap of about three quarters of a standard deviation and falls above it,
and separately that the props where we disagree most with the book are the ones
we are most often wrong about. If that holds here, then "elite = biggest
disagreement" is backwards, and elite should be the band where accuracy peaks
rather than the tail. The table below will say which.

Deduplicated to one row per distinct prop. The board publishes a row per
bookmaker, so the same right-or-wrong answer is otherwise counted up to three
times and every interval comes out narrower than the evidence earns.

Read-only, writes nothing.

    docker compose run --rm -e SOURCE=live training python accuracy_by_gap.py
    docker compose run --rm -e SOURCE=backtest training python accuracy_by_gap.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "live")
SINCE = os.getenv("SINCE", "")
BANDS = int(os.getenv("BANDS", "5"))
MIN_CELL = int(os.getenv("MIN_CELL", "25"))


def band_table(g, col, label, clusters):
    """Hit rate by band of `col`, with an interval on each band."""
    v = g[col].replace([np.inf, -np.inf], np.nan)
    ok = v.notna()
    if ok.sum() < MIN_CELL * 2 or v[ok].nunique() < BANDS:
        return
    q = pd.qcut(v[ok].rank(method="first"), BANDS, labels=False)
    print(f"    {label}")
    print(f"      {'band':<10}{'range':<22}{'picks':>7}{'correct':>9}  95% CI")
    sub = g[ok]
    for i in range(BANDS):
        cell = sub[q == i]
        if len(cell) < MIN_CELL:
            print(f"      {i + 1:<10}{'':<22}{len(cell):>7}{'-':>9}")
            continue
        w = cell["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda idx: float(np.mean(w[idx])),
            clusters[cell.index], n_boot=1500)
        rng = f"{v[cell.index].min():.2f} to {v[cell.index].max():.2f}"
        print(f"      {i + 1:<10}{rng:<22}{len(cell):>7}{r:>9.1%}"
              f"  [{lo:>5.1%},{hi:>5.1%}]")
    # Does accuracy actually rise with the gap? Strict monotonicity is the
    # wrong test: with a couple of hundred picks a band, a genuinely rising
    # curve fails it on noise alone, and a flat one passes it by luck. Checked
    # on synthetic curves, "rises: yes/no" called a real rising signal NO and a
    # pure coin flip's first band the peak.
    #
    # Two statistics instead, both of which survive that. The rank correlation
    # between the gap and being right, which uses every pick rather than five
    # bucket means. And the top band minus the bottom band with an interval on
    # the difference, which is the claim the tiers actually make: that the
    # widest disagreements are the ones to bet.
    means = [sub[q == i]["hit"].mean() for i in range(BANDS)
             if (q == i).sum() >= MIN_CELL]
    if len(means) >= 3:
        rho = float(pd.Series(v[ok].rank()).corr(
            pd.Series(sub["hit"].to_numpy()), method="spearman"))
        top_cell, bot_cell = sub[q == BANDS - 1], sub[q == 0]
        if len(top_cell) >= MIN_CELL and len(bot_cell) >= MIN_CELL:
            a = top_cell["hit"].to_numpy()
            b = bot_cell["hit"].to_numpy()
            idx = np.concatenate([top_cell.index.to_numpy(),
                                  bot_cell.index.to_numpy()])
            flags = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
            vals = np.concatenate([a, b])
            diff, dlo, dhi = S.clustered_bootstrap(
                lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                           - float(np.mean(vals[i][flags[i] == 0]))
                           if (flags[i] == 1).any() and (flags[i] == 0).any()
                           else 0.0),
                clusters[idx], n_boot=1500)
            print(f"      widest band minus narrowest: {diff:+.1%} "
                  f"[{dlo:+.1%}, {dhi:+.1%}]")
        print(f"      rank correlation of gap with being right: {rho:+.3f}"
              f"   most accurate band: {int(np.argmax(means)) + 1} of {len(means)}")
    print()



def build_tiers(fit, test, col, clusters_fit, clusters_test, shares=(0.15, 0.20, 0.25)):
    """Cut tiers so elite is whatever hit most, then check it on unseen rows.

    The instruction is exactly this: bucket however you like, as long as elite
    is historically the band that hits the most. So the gap is split into fine
    bands on the fitting rows, the bands are ORDERED BY ACCURACY rather than by
    size of disagreement, and the most accurate ones are called elite until they
    hold `shares[0]` of the picks, the next strong, and so on.

    The bands a tier owns need not be next to each other, and that is the point.
    If accuracy peaks in the middle of the gap range and falls away at the
    extremes, which is what this project found on both halves of a previous
    season, then elite is a middle band and the hugest disagreements are not
    elite at all. A contiguous "bigger is better" cut cannot express that.

    Fitted and tested on different rows on purpose. Ordering bands by their own
    accuracy and then reporting that accuracy is circular: the top band is top
    partly because it got lucky. The fit population decides the boundaries, the
    test population says whether they mean anything, and only the second number
    is worth quoting.
    """
    v_fit = fit[col].replace([np.inf, -np.inf], np.nan)
    ok = v_fit.notna()
    if ok.sum() < 200:
        return None
    nb = min(10, max(4, int(ok.sum() // 60)))
    edges = np.unique(np.quantile(v_fit[ok], np.linspace(0, 1, nb + 1)))
    if len(edges) < 4:
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    band_of = lambda x: np.clip(np.searchsorted(edges, x, side="right") - 1,
                                0, len(edges) - 2)

    fb = band_of(v_fit.fillna(edges[1]).to_numpy())
    acc = {}
    for b in range(len(edges) - 1):
        m = fb == b
        if m.sum() >= 25:
            acc[b] = float(fit.loc[m, "hit"].mean())
    if len(acc) < 4:
        return None

    order = sorted(acc, key=lambda b: -acc[b])
    counts = {b: int((fb == b).sum()) for b in order}
    total = sum(counts.values())
    names, assign, used = ["elite", "strong", "medium"], {}, 0
    for name, share in zip(names, shares):
        target = share * total
        taken = 0
        while used < len(order) and taken < target:
            assign[order[used]] = name
            taken += counts[order[used]]
            used += 1
    for b in order[used:]:
        assign[b] = "small"

    v_test = test[col].replace([np.inf, -np.inf], np.nan)
    tb = band_of(v_test.fillna(edges[1]).to_numpy())
    test = test.assign(new_tier=[assign.get(b, "small") for b in tb])

    print(f"  tiers cut on {col}, fitted on {len(fit):,} rows, "
          f"checked on {len(test):,}")
    print(f"    {'tier':<10}{'gap ranges it owns':<34}{'picks':>7}{'share':>8}"
          f"{'correct':>9}  95% CI")
    for name in ("elite", "strong", "medium", "small"):
        bands = sorted(b for b, nm in assign.items() if nm == name)
        if not bands:
            continue
        rng = ", ".join(
            f"{'' if np.isinf(edges[b]) else f'{edges[b]:.2f}'}"
            f"-{'' if np.isinf(edges[b + 1]) else f'{edges[b + 1]:.2f}'}"
            for b in bands)
        cell = test[test["new_tier"] == name]
        if len(cell) < 25:
            print(f"    {name:<10}{rng[:33]:<34}{len(cell):>7}{'-':>8}{'-':>9}")
            continue
        w = cell["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w[i])), clusters_test[cell.index],
            n_boot=2000)
        print(f"    {name:<10}{rng[:33]:<34}{len(cell):>7}"
              f"{len(cell)/len(test):>8.0%}{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]")
    return test



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
    # Ask the database what it has rather than trusting a memory of it.
    #
    # This selected `stddev`, which prop_edge_results does not have, and the
    # whole run died on it. The table has been extended by eight separate
    # migrations and no single file describes it, so the columns are read and
    # intersected with the ones wanted.
    with eng.connect() as c:
        have = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'prop_edge_results'"))}
    want = ["market_code", "game_date", "player_name", "line",
            "recommended_side", "edge_tier", "hit", "gap_z",
            "projection_median", "q25", "q75", "source"]
    missing = [c for c in want if c not in have]
    cols = [c for c in want if c in have]
    for required in ("hit", "line", "projection_median", "market_code"):
        if required not in cols:
            raise SystemExit(f"prop_edge_results has no {required} column")
    if missing:
        print(f"  note: {', '.join(missing)} not in this table, skipping them")
    d = pd.read_sql(text("SELECT " + ", ".join(cols) +
                         " FROM prop_edge_results WHERE " +
                         " AND ".join(where)), eng, params=params)
    d = d.rename(columns={"projection_median": "p50"})
    if d.empty:
        raise SystemExit("no graded rows for that population")

    before = len(d)
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line", "recommended_side"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    clusters = d["game_date"].astype(str).to_numpy()

    # Three ways to say "how far apart are we", all price-free.
    d["gap_abs"] = (d["p50"] - d["line"]).abs()
    d["gap_pct"] = d["gap_abs"] / d["line"].abs().clip(lower=0.5)
    # Spread from the published quantile ladder, which is what the site shows,
    # rather than a stddev column that does not exist on this table.
    if {"q25", "q75"}.issubset(d.columns):
        iqr = (d["q75"] - d["q25"]).abs()
        d["gap_sd"] = d["gap_abs"] / iqr.clip(lower=0.1)
    else:
        d["gap_sd"] = np.nan

    print(f"population: source={SOURCE or 'any'}  since={SINCE or 'all time'}")
    print(f"  {before:,} graded rows -> {len(d):,} distinct props")
    print(f"  overall accuracy {d['hit'].mean():.1%}")
    print()

    print("the tier the board publishes today, which is cut on the probability")
    print("edge rather than on the gap:")
    print(f"  {'tier':<12}{'picks':>7}{'correct':>9}  95% CI")
    for tier in ("elite", "strong", "medium", "small"):
        cell = d[d["edge_tier"] == tier]
        if len(cell) < MIN_CELL:
            continue
        w = cell["hit"].to_numpy()
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(w[i])), clusters[cell.index], n_boot=2000)
        print(f"  {tier:<12}{len(cell):>7}{r:>9.1%}  [{lo:>5.1%},{hi:>5.1%}]")
    print()

    print("accuracy by the size of the disagreement, every market pooled:")
    for col, label in (("gap_pct", "gap as a share of the line"),
                       ("gap_sd", "gap over the published interval width"),
                       ("gap_z", "gap_z as the board stores it"),
                       ("gap_abs", "gap in raw units")):
        if col in d.columns:
            band_table(d, col, label, clusters)

    # The deliverable: tiers where elite is whatever hits most, fitted on the
    # backtest and checked on the live season it has never seen.
    print("=" * 70)
    print("TIERS CUT SO ELITE IS THE BAND THAT HITS MOST")
    print("=" * 70)
    fit = pd.read_sql(text("SELECT " + ", ".join(cols) +
                           " FROM prop_edge_results WHERE source = 'backtest'"
                           " AND hit IS NOT NULL AND line IS NOT NULL"
                           " AND projection_median IS NOT NULL"), eng)
    if fit.empty:
        print("  no backtest rows to fit the boundaries on")
    else:
        fit = fit.rename(columns={"projection_median": "p50"})
        fit = fit.drop_duplicates(subset=["game_date", "player_name",
                                          "market_code", "line",
                                          "recommended_side"]).reset_index(drop=True)
        fit["hit"] = fit["hit"].astype(float)
        fit["gap_abs"] = (fit["p50"] - fit["line"]).abs()
        fit["gap_pct"] = fit["gap_abs"] / fit["line"].abs().clip(lower=0.5)
        cf = fit["game_date"].astype(str).to_numpy()
        for col in ("gap_pct", "gap_abs"):
            build_tiers(fit, d, col, cf, clusters)
            print()

    print("and per market, on the share-of-line gap:")
    for m in sorted(d["market_code"].unique()):
        g = d[d["market_code"] == m]
        if len(g) < MIN_CELL * 3:
            print(f"  {m:<20}{len(g):>6} props, too few to split")
            continue
        print(f"  {m}   {len(g)} props   overall {g['hit'].mean():.1%}")
        band_table(g, "gap_pct", "    by gap as a share of the line", clusters)


if __name__ == "__main__":
    main()
