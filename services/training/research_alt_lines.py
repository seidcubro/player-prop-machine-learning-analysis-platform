"""Would alternate lines be a better product than the main over/under?

The argument for them is sound and worth testing properly. A main line is set
where the book wants two-sided action, which is near the middle of the outcome
distribution, so it is a coin flip by construction and the vig decides. An
alternate line well below it is not a coin flip: the question stops being "which
side of the middle" and becomes "does this player clear a floor". That is a much
easier question and it is one a distribution can answer, which is the one thing
this project actually has.

Two things have to be true before it is worth buying alternate markets, and only
the first can be answered without spending credits:

  1. The floor has to hit more often than the price demands. That is a base
     rate and this measures it, from main lines and real outcomes, with no
     model and no odds involved.

  2. Our number has to add something to the base rate. If every player clears
     80% of his line 78% of the time, the book prices it at -350 and there is
     nothing there. The edge exists only if we can say WHICH players clear it
     more often than that. So every base rate below is reported again split by
     whether our own median sits above the threshold.

The second column is the whole product. A base rate is something the book
already knows.

Also reports the fair American price at each threshold, so a quoted alternate
line can be compared against it directly rather than by feel.

Read-only, writes nothing.

    docker compose run --rm training python research_alt_lines.py
    docker compose run --rm -e SOURCE=live -e SINCE=2026-09-01 training \\
        python research_alt_lines.py
"""

import os

import numpy as np
import pandas as pd
import stats_ci as S
from sqlalchemy import create_engine, text

import build_prop_edges as bp

SOURCE = os.getenv("SOURCE", "")
SINCE = os.getenv("SINCE", "")
MIN_N = int(os.getenv("MIN_N", "120"))

# Yardage lines move in yards and are worth expressing as a fraction of the
# line. Counts do not: 80% of a 3.5 reception line is 2.8, which is not a line
# anybody posts, so those step down whole receptions.
FRACTIONAL = {"rec_yds", "rush_yds", "pass_yds"}
FRACTIONS = (0.50, 0.60, 0.70, 0.80, 0.90, 1.00)
STEPS = (-3, -2, -1, 0)


def fair_american(p: float) -> str:
    """The price at which a probability p is break-even."""
    if p <= 0.0 or p >= 1.0:
        return "-"
    if p >= 0.5:
        return f"{-100.0 * p / (1.0 - p):,.0f}"
    return f"+{100.0 * (1.0 - p) / p:,.0f}"


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    where = ["actual IS NOT NULL", "line IS NOT NULL",
             "projection_median IS NOT NULL"]
    params = {}
    if SOURCE:
        where.append("source = :source")
        params["source"] = SOURCE
    if SINCE:
        where.append("game_date >= :since")
        params["since"] = SINCE
    d = pd.read_sql(text("""
        SELECT market_code, game_date, source, line, actual,
               projection_median AS p50
        FROM prop_edge_results
        WHERE """ + " AND ".join(where)), eng, params=params)
    if d.empty:
        raise SystemExit("no graded rows for that population")

    d["cluster"] = d["game_date"].astype(str)
    print(f"population: source={SOURCE or 'any'}  since={SINCE or 'all time'}  "
          f"{len(d):,} graded props")
    for r in d.groupby("source").size().items():
        print(f"  {str(r[0]):<10}{r[1]:>7} rows")
    print()
    print("  'base' is every prop at that threshold. 'ours' is the subset where")
    print("  our median already sits above it, which is the only column that can")
    print("  beat a price the book sets from the same base rate.")
    print()

    for market in sorted(d["market_code"].unique()):
        g = d[d["market_code"] == market]
        if len(g) < MIN_N:
            continue
        frac = market in FRACTIONAL
        grid = FRACTIONS if frac else STEPS
        print(f"{market}   n={len(g):,}   mean line {g['line'].mean():.1f}   "
              f"mean outcome {g['actual'].mean():.1f}")
        print(f"  {'threshold':<14}{'base hit':>10}{'fair':>8}"
              f"{'ours n':>9}{'ours hit':>10}{'fair':>8}  95% CI on ours")
        for step in grid:
            thr = g["line"] * step if frac else g["line"] + step
            label = f"{step:.0%} of line" if frac else f"line {step:+d}"
            over = (g["actual"] > thr).to_numpy()
            base = float(over.mean())
            sel = (g["p50"] > thr).to_numpy()
            n_sel = int(sel.sum())
            if n_sel >= 50:
                w = over[sel]
                r, lo, hi = S.clustered_bootstrap(
                    lambda i: float(np.mean(w[i])),
                    g["cluster"].to_numpy()[sel], n_boot=1500)
                ours = (f"{n_sel:>9,}{r:>10.1%}{fair_american(r):>8}"
                        f"  [{lo:>5.1%},{hi:>5.1%}]")
            else:
                ours = f"{n_sel:>9,}{'-':>10}{'-':>8}"
            print(f"  {label:<14}{base:>10.1%}{fair_american(base):>8}{ours}")
        print()

    # The one that matters most, stated on its own: does our median separate
    # the players who clear a deep floor from the ones who do not? If the two
    # columns above are the same number, the ladder is adding nothing and an
    # alternate line is just a short price with no edge in it.
    print("lift: how much our median adds over the base rate, per threshold")
    print(f"  {'market':<12}{'threshold':<14}{'base':>8}{'ours':>8}{'lift':>8}"
          f"{'n':>8}")
    for market in sorted(d["market_code"].unique()):
        g = d[d["market_code"] == market]
        if len(g) < MIN_N:
            continue
        frac = market in FRACTIONAL
        for step in (FRACTIONS if frac else STEPS):
            thr = g["line"] * step if frac else g["line"] + step
            label = f"{step:.0%}" if frac else f"{step:+d}"
            over = (g["actual"] > thr).to_numpy()
            sel = (g["p50"] > thr).to_numpy()
            if sel.sum() < 50:
                continue
            base, ours = float(over.mean()), float(over[sel].mean())
            print(f"  {market:<12}{label:<14}{base:>8.1%}{ours:>8.1%}"
                  f"{ours - base:>+8.1%}{int(sel.sum()):>8,}")


if __name__ == "__main__":
    main()
