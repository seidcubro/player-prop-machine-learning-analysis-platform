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
               edge_tier, hit, gap_z, projection_median AS p50, stddev
        FROM prop_edge_results
        WHERE """ + " AND ".join(where)), eng, params=params)
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
    d["gap_sd"] = d["gap_abs"] / d["stddev"].abs().clip(lower=0.1)

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
                       ("gap_sd", "gap in standard deviations"),
                       ("gap_z", "gap_z as the board stores it"),
                       ("gap_abs", "gap in raw units")):
        band_table(d, col, label, clusters)

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
