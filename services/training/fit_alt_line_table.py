"""How often a player clears a floor, measured rather than modelled.

research_alt_lines.py settled what our own number is worth at an alternate line:
almost nothing. Weighted across 22,357 props at floors of 50% to 70% of the main
line, our median adds +0.26% over the base rate. A book setting an alternate
price from the same base rate gives up nothing to us there.

That is not the end of the idea, it changes whose job it is. If the true rate at
50% of a receiving line is 72.2%, the fair price is -260, and a book quoting -200
is handing over eight points of edge whether or not we have any opinion about
the player. That is a price scan against a measured base rate, and it needs no
model at all, which is exactly why it can be trusted: there is nothing in it to
overfit.

So this builds the table that scan needs, and then does the only thing that makes
it believable. The rates are fitted on the walk-forward backtest rows and
checked against the live season. A base rate that does not replicate out of
sample is a historical curiosity, not a price.

Bucketed by line size, because the rate is not constant across one. A 12 yard
receiving line and a 70 yard one are different questions: a fifth-option tight
end clears half of 12 yards on one catch, and a number one receiver clearing
half of 70 needs a real game. Pooling them averages two populations and prices
neither.

Writes artifacts/alt_line_base_rates.json with WRITE=1.

    docker compose run --rm training python fit_alt_line_table.py
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
OUT = ARTIFACTS / "alt_line_base_rates.json"

MIN_CELL = int(os.getenv("MIN_CELL", "80"))
N_BUCKETS = int(os.getenv("N_BUCKETS", "3"))

FRACTIONAL = {"rec_yds", "rush_yds", "pass_yds"}
FRACTIONS = (0.50, 0.60, 0.70, 0.80, 0.90)
STEPS = (-3, -2, -1)


def fair_american(p: float):
    if not (0.0 < p < 1.0):
        return None
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def fmt_price(p):
    a = fair_american(p)
    return "-" if a is None else (f"{a:,.0f}" if a < 0 else f"+{a:,.0f}")


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT market_code, game_date, source, line, actual
        FROM prop_edge_results
        WHERE actual IS NOT NULL AND line IS NOT NULL
    """), eng)
    if d.empty:
        raise SystemExit("no graded rows")
    d["cluster"] = d["game_date"].astype(str)

    fit = d[d["source"] == "backtest"]
    live = d[d["source"] == "live"]
    print(f"{len(fit):,} backtest rows to fit, {len(live):,} live rows to check")
    print(f"line-size buckets per market: {N_BUCKETS}, "
          f"minimum {MIN_CELL} rows a cell")
    print()
    print(f"  {'market':<10}{'line band':<16}{'floor':<8}{'n':>7}{'rate':>8}"
          f"{'fair':>9}  95% CI{'':<8}{'live n':>8}{'live':>8}{'drift':>8}")

    table = {}
    for market in sorted(d["market_code"].unique()):
        gf = fit[fit["market_code"] == market]
        gl = live[live["market_code"] == market]
        if len(gf) < MIN_CELL * N_BUCKETS:
            continue
        frac = market in FRACTIONAL
        grid = FRACTIONS if frac else STEPS

        # Buckets from the fitting rows only, then applied to the live rows, so
        # the live column is scored against the band it would have been priced
        # in rather than one drawn to fit it.
        edges = np.unique(np.quantile(gf["line"], np.linspace(0, 1, N_BUCKETS + 1)))
        if len(edges) < 3:
            continue
        edges[0], edges[-1] = -np.inf, np.inf

        for b in range(len(edges) - 1):
            lo, hi = edges[b], edges[b + 1]
            cf = gf[(gf["line"] >= lo) & (gf["line"] < hi)]
            cl = gl[(gl["line"] >= lo) & (gl["line"] < hi)]
            if len(cf) < MIN_CELL:
                continue
            band = (f"{'' if np.isinf(lo) else f'{lo:g}'}"
                    f"-{'' if np.isinf(hi) else f'{hi:g}'}")
            for step in grid:
                thr_f = cf["line"] * step if frac else cf["line"] + step
                hit = (cf["actual"] > thr_f).to_numpy()
                rate = float(hit.mean())
                r, clo, chi = S.clustered_bootstrap(
                    lambda i: float(np.mean(hit[i])),
                    cf["cluster"].to_numpy(), n_boot=1500)
                if len(cl) >= 40:
                    thr_l = cl["line"] * step if frac else cl["line"] + step
                    lrate = float((cl["actual"] > thr_l).mean())
                    live_cols = (f"{len(cl):>8,}{lrate:>8.1%}"
                                 f"{lrate - rate:>+8.1%}")
                else:
                    lrate = None
                    live_cols = f"{len(cl):>8,}{'-':>8}{'-':>8}"
                label = f"{step:.0%}" if frac else f"{step:+d}"
                print(f"  {market:<10}{band:<16}{label:<8}{len(cf):>7}"
                      f"{rate:>8.1%}{fmt_price(rate):>9}  "
                      f"[{clo:>5.1%},{chi:>5.1%}]{'':<2}{live_cols}")
                table.setdefault(market, []).append({
                    "line_min": None if np.isinf(lo) else float(lo),
                    "line_max": None if np.isinf(hi) else float(hi),
                    "kind": "fraction" if frac else "step",
                    "step": float(step),
                    "rate": rate,
                    "ci": [float(clo), float(chi)],
                    "n": int(len(cf)),
                    "fair_american": fair_american(rate),
                    "live_rate": lrate,
                    "live_n": int(len(cl)),
                })
        print()

    # Does the backtest rate predict the live one? If not, the table is history.
    pairs = [(c["rate"], c["live_rate"], c["live_n"])
             for cells in table.values() for c in cells
             if c["live_rate"] is not None]
    if pairs:
        a = np.array([p[0] for p in pairs])
        b = np.array([p[1] for p in pairs])
        w = np.array([p[2] for p in pairs], dtype=float)
        drift = float(np.average(b - a, weights=w))
        print(f"replication: {len(pairs)} cells with a live sample")
        print(f"  correlation backtest to live  {np.corrcoef(a, b)[0, 1]:.3f}")
        print(f"  mean absolute difference      {np.average(np.abs(b - a), weights=w):.1%}")
        print(f"  weighted drift                {drift:+.1%}")
        print("  A table that does not replicate is a historical curiosity, not")
        print("  a price. Correlation near 1 with small drift is what makes this")
        print("  usable; a large drift means the rate itself moves by season and")
        print("  the floor has to be refitted, not just looked up.")

    if os.getenv("WRITE") == "1":
        OUT.write_text(json.dumps(table, indent=2), encoding="utf-8")
        print(f"\nwrote {OUT.name}, {sum(len(v) for v in table.values())} cells")
    else:
        print("\nnothing written. WRITE=1 to save the table.")


if __name__ == "__main__":
    main()
