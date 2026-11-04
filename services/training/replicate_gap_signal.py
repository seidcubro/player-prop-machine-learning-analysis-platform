"""Does each market's gap signal hold in BOTH halves of the live season?

Ten bands on eight weeks turned up two results whose intervals clear zero, and
they point opposite ways:

    rush_att  top decile 74.1%   top minus bottom +37.0% [+13.6%, +61.2%]
    rush_yds  top decile 39.5%   top minus bottom -26.3% [-50.6%,  -5.3%]
    recs      top decile 68.0%   top minus bottom +10.7% [ -0.5%, +22.4%]
    rec_yds                      top minus bottom  -0.8% [-21.6%, +22.6%]
    POOLED                       top minus bottom  +1.2% [ -7.0%, +13.2%]

Pooled is flat, rank correlation +0.002, because two markets are pointing in
opposite directions and cancelling. The earlier pooled +7.9% [+1.7%, +21.2%]
was five bands on seven weeks and does not survive ten bands on eight.

None of that is a finding yet. Six markets were measured, the two that cleared
were picked out afterwards, and "the widest disagreements are where we are most
wrong" has appeared in this project before and then failed to replicate. A
result selected after the fact needs a second, independent look before it goes
anywhere near a board.

So the live season is cut in half by date and each market is measured twice.
The question is not whether a half clears zero, which neither half has the rows
to do, but whether the two halves AGREE: same sign, similar size. A real
relationship shows up in both. A lucky decile shows up in one and vanishes.

Both ends are tested because rush_yds, if it is real, is a signal pointing
backwards and would be used by flipping the tier, not by discarding the market.

Read-only.

    docker compose run --rm training python replicate_gap_signal.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "live")
SEASON = int(os.getenv("SEASON", "2026"))
SHARE = float(os.getenv("SHARE", "0.20"))
MIN_SIDE = int(os.getenv("MIN_SIDE", "20"))
# How large each half has to be before "they agree" means anything.
#
# Agreement alone is a weak test: at these counts two halves of PURE NOISE
# agree in sign with both sides above 3 points in 28% of trials, which would
# have passed roughly two of the six markets by luck. Simulated at 190 props a
# half against a real 15 point effect, a real 8 point effect, and nothing:
#
#     threshold      +15pt    +8pt    noise
#        3%            78%     51%      28%
#        5%            72%     40%      17%
#        8%            53%     23%       7%
#       12%            35%     11%       2%
#
# Eight points keeps most of a genuinely large effect and cuts the false
# positive rate to about one in fourteen. It will miss a real but small signal,
# which is the right way round: a tier label is a promise to somebody betting.
MIN_EFFECT = float(os.getenv("MIN_EFFECT", "0.08"))


def load(eng):
    d = pd.read_sql(text("""
        SELECT market_code, game_date, player_name, line, hit,
               projection_median AS p50
        FROM prop_edge_results
        WHERE source = :s AND hit IS NOT NULL AND line IS NOT NULL
          AND projection_median IS NOT NULL AND game_date >= :since
        ORDER BY game_date
    """), eng, params={"s": SOURCE, "since": f"{SEASON}-08-01"})
    d = d.drop_duplicates(subset=["game_date", "player_name", "market_code",
                                  "line"]).reset_index(drop=True)
    d["hit"] = d["hit"].astype(float)
    d["gap"] = (d["p50"] - d["line"]).abs() / d["line"].abs().clip(lower=0.5)
    d["cluster"] = d["game_date"].astype(str)
    return d


def band_vs_rest(g, high):
    """Top (or bottom) SHARE of this slice against the rest of it."""
    v = g["gap"]
    thr = v.quantile(1.0 - SHARE if high else SHARE)
    sel = (v >= thr) if high else (v <= thr)
    a, b = g.loc[sel, "hit"].to_numpy(), g.loc[~sel, "hit"].to_numpy()
    if len(a) < MIN_SIDE or len(b) < MIN_SIDE:
        return None
    vals = np.concatenate([a, b])
    flags = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
    cl = np.concatenate([g.loc[sel, "cluster"].to_numpy(),
                         g.loc[~sel, "cluster"].to_numpy()])
    r, lo, hi = S.clustered_bootstrap(
        lambda i: (float(np.mean(vals[i][flags[i] == 1]))
                   - float(np.mean(vals[i][flags[i] == 0]))
                   if (flags[i] == 1).any() and (flags[i] == 0).any() else 0.0),
        cl, n_boot=1500)
    return {"n": int(sel.sum()), "acc": float(a.mean()), "d": r,
            "lo": lo, "hi": hi}


def cell(r):
    return (f"{'-':>26}" if r is None
            else f"{r['n']:>5}{r['acc']:>8.1%}{r['d']:>+9.1%}  ")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = load(eng)
    if d.empty:
        raise SystemExit(f"no graded {SOURCE} rows for {SEASON}")

    cut = d["game_date"].quantile(0.5)
    first, second = d[d["game_date"] <= cut], d[d["game_date"] > cut]
    print(f"{len(d):,} distinct graded props, {SOURCE} {SEASON}")
    print(f"  first half  to {cut}: {len(first):,}")
    print(f"  second half after    : {len(second):,}")
    print(f"  top/bottom {SHARE:.0%} of each market, within each half")
    print()
    print("  A half on its own cannot clear zero at these counts, and is not")
    print("  asked to. The question is whether the two halves agree.")
    print()
    print(f"  {'market':<11}{'end':<7}"
          f"{'   n    acc     diff  ':>26}{'   n    acc     diff  ':>26}"
          f"  agree?")
    print(f"  {'':<11}{'':<7}{'first half':>26}{'second half':>26}")

    verdicts = []
    for m in sorted(d["market_code"].unique()):
        g1, g2 = first[first["market_code"] == m], second[second["market_code"] == m]
        if len(g1) < MIN_SIDE * 3 or len(g2) < MIN_SIDE * 3:
            print(f"  {m:<11}{'-':<7}{'too few rows in one half':>52}")
            continue
        for label, high in (("top", True), ("bottom", False)):
            r1, r2 = band_vs_rest(g1, high), band_vs_rest(g2, high)
            if r1 is None or r2 is None:
                print(f"  {m:<11}{label:<7}{cell(r1)}{cell(r2)}  -")
                continue
            same = (r1["d"] > 0) == (r2["d"] > 0)
            both = abs(r1["d"]) > MIN_EFFECT and abs(r2["d"]) > MIN_EFFECT
            mark = ("yes" if same and both else
                    f"same sign, under {MIN_EFFECT:.0%}" if same else "NO")
            print(f"  {m:<11}{label:<7}{cell(r1)}{cell(r2)}  {mark}")
            if same and both:
                verdicts.append((m, label, r1["d"], r2["d"]))
        print()

    print("=" * 70)
    if not verdicts:
        print("Nothing replicated across both halves.")
        print()
        print("The two results that cleared zero on the full season did not")
        print("survive being asked twice, which is what selecting a decile")
        print("after seeing it looks like. The tiers stay as they are.")
        return
    print("Held in both halves, same direction, both worth more than 3 points:")
    print(f"  {'market':<11}{'end':<8}{'first':>9}{'second':>9}")
    for m, label, d1, d2 in verdicts:
        print(f"  {m:<11}{label:<8}{d1:>+9.1%}{d2:>+9.1%}")
    print()
    print("A bottom-end result is a signal pointing backwards: for that market")
    print("the NARROWEST disagreements are the ones to grade elite, which is a")
    print("flipped tier rather than a dropped market. Anything here still has")
    print("to clear fit_gap_tiers before it ships.")


if __name__ == "__main__":
    main()
