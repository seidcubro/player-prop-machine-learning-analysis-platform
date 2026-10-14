"""Is the board wrong about this season in one direction, and does fixing it pay?

Every correction in the stack is fitted on seasons that have finished and applied
to one that has not. The point models train on 2022-2025. The quantile PIT map is
fitted before 2025 and measured on 2025. The spread calibrator's chronological
split ends 2025-12-07. The probability calibrator pools every graded row.

And 2026 is not behaving like any of them:

    market      projection  median   line   actual   proj-act   p50-act
    pass_yds         217.8   205.6  223.3    229.5      -11.7     -23.9
    rec_yds           32.8    26.5   30.5     36.0       -3.3      -9.5
    rush_yds          34.3    29.6   33.0     35.2       -1.0       -5.6

The board says overs happen 40.6% of the time. They happen 51.9% of the time.
That single fact explains the season: a probability 11 points low makes every
expected value low on the over side and high on the under side, the filter is
handed inflated numbers, and it selects bets that are negative. 1,279 live
graded props, -84.74 units.

Recency weighting in the probability calibrator does not reach it. Measured on
the 2026 holdout, re-weighting the seasons before it moves the calibrated mean
by well under a point, because no past season ran as hot as this one: you cannot
reweight your way to a level you have never seen.

So this measures the one thing none of those artifacts can see, the current
season's own level, and then checks whether using it would have made money.

WALK-FORWARD, never in-sample. For each game date in order, the bias is fitted
on live graded rows strictly before that date and applied to that date's rows.
Week 1 corrects by nothing, because nothing has been graded yet. That is the
only way to ask "would this have helped", as opposed to "does it describe what
already happened", and the difference is the whole reason this project keeps a
noise rule.

Shrunk toward zero by sample size, so an early-season handful of rows cannot
swing a market:

    bias = sum(actual - median) / (n + PRIOR_N)

PRIOR_N is the number of rows at which the estimate is believed half as much as
the raw mean. With n = 50 and PRIOR_N = 150 a market moves a quarter of the way
to its measured bias, which is the right amount of caution for week three.

A flipped pick has no stored price for the side it flipped to, so those are
scored at -110. Picks that do not flip keep the price actually taken. Both the
flat-price and real-price figures are printed so the assumption is visible.

Writes nothing unless WRITE=1, and then only markets that improved.

    docker compose run --rm training python fit_season_bias.py
    docker compose run --rm -e WRITE=1 training python fit_season_bias.py
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
OUT = ARTIFACTS / "season_bias.json"

SEASON = int(os.getenv("SEASON", "2026"))
PRIOR_N = float(os.getenv("PRIOR_N", "150"))
MIN_ROWS = int(os.getenv("MIN_ROWS", "150"))
FLAT_PRICE = -110.0


def payout(american):
    a = np.asarray(american, dtype=float)
    return np.where(a > 0, a / 100.0, 100.0 / np.abs(a))


def main():
    eng = create_engine(bp.DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT market_code, game_date, recommended_side, edge_tier,
               projection, projection_median AS p50, line, actual,
               hit, price_american
        FROM prop_edge_results
        WHERE source = 'live'
          AND hit IS NOT NULL AND actual IS NOT NULL
          AND line IS NOT NULL AND projection_median IS NOT NULL
          AND game_date >= :since
        ORDER BY game_date
    """), eng, params={"since": f"{SEASON}-08-01"})
    if d.empty:
        raise SystemExit(f"no live graded rows for {SEASON}")

    d["game_date"] = pd.to_datetime(d["game_date"])
    d["resid"] = d["actual"] - d["p50"]
    dates = sorted(d["game_date"].unique())

    print(f"{len(d):,} live graded props, {SEASON} season, "
          f"{len(dates)} game dates")
    print(f"shrinkage prior {PRIOR_N:g} rows, minimum {MIN_ROWS} rows to correct "
          f"a market")
    print()

    rows = []
    for dt in dates:
        past = d[d["game_date"] < dt]
        now = d[d["game_date"] == dt].copy()
        # One bias per market, from that market's own history only.
        bias = {}
        for m, g in past.groupby("market_code"):
            if len(g) < MIN_ROWS:
                continue
            bias[m] = float(g["resid"].sum() / (len(g) + PRIOR_N))
        now["bias"] = now["market_code"].map(bias).fillna(0.0)
        rows.append(now)
    d = pd.concat(rows, ignore_index=True)

    d["p50_adj"] = d["p50"] + d["bias"]
    d["side_adj"] = np.where(d["p50_adj"] > d["line"], "over", "under")
    d["flipped"] = d["side_adj"] != d["recommended_side"]
    over_hit = d["actual"] > d["line"]
    d["hit_adj"] = np.where(d["side_adj"] == "over", over_hit, ~over_hit)

    pay_real = payout(d["price_american"])
    pay_flat = payout(np.full(len(d), FLAT_PRICE))
    # A flipped pick has no stored price for its new side, so it is scored flat.
    pay_adj = np.where(d["flipped"], pay_flat, pay_real)
    d["units_base"] = np.where(d["hit"].astype(bool), pay_real, -1.0)
    d["units_adj"] = np.where(d["hit_adj"], pay_adj, -1.0)
    d["units_base_flat"] = np.where(d["hit"].astype(bool), pay_flat, -1.0)
    d["units_adj_flat"] = np.where(d["hit_adj"], pay_flat, -1.0)
    d["cluster"] = d["game_date"].astype(str)

    def line_for(sub, label):
        if len(sub) < 30:
            return
        b, a = sub["units_base"].to_numpy(), sub["units_adj"].to_numpy()
        diff = a - b
        r, lo, hi = S.clustered_bootstrap(
            lambda i: float(np.mean(diff[i])), sub["cluster"].to_numpy(), n_boot=2000)
        print(f"  {label:<18}{len(sub):>7}{sub['hit'].mean():>8.1%}"
              f"{sub['hit_adj'].mean():>9.1%}{sub['flipped'].mean():>9.1%}"
              f"{b.mean():>+9.1%}{a.mean():>+9.1%}"
              f"{r:>+9.1%} [{lo:>+6.1%},{hi:>+6.1%}]")

    print(f"  {'population':<18}{'n':>7}{'hit':>8}{'hit adj':>9}"
          f"{'flipped':>9}{'ROI':>9}{'ROI adj':>9}{'change':>9}  95% CI")
    line_for(d, "everything")
    pub = d[d["edge_tier"].isin(("elite", "strong", "medium"))]
    line_for(pub, "published tiers")
    for tier in ("elite", "strong", "medium", "small"):
        line_for(d[d["edge_tier"] == tier], f"  {tier}")
    print()
    for m in sorted(d["market_code"].unique()):
        line_for(d[d["market_code"] == m], m)

    print()
    print("  the same comparison at a flat -110 on both sides, which removes the "
          "price assumption entirely:")
    bf = d["units_base_flat"].mean()
    af = d["units_adj_flat"].mean()
    print(f"    baseline {bf:+.1%}   corrected {af:+.1%}   change {af - bf:+.1%}")

    # What the final bias would be, per market, fitted on the whole season.
    print()
    print(f"  {'market':<18}{'n':>7}{'mean resid':>12}{'shrunk bias':>13}"
          f"{'median':>9}{'line':>9}")
    final = {}
    for m, g in d.groupby("market_code"):
        if len(g) < MIN_ROWS:
            continue
        raw = float(g["resid"].mean())
        shrunk = float(g["resid"].sum() / (len(g) + PRIOR_N))
        final[m] = shrunk
        print(f"  {m:<18}{len(g):>7}{raw:>+12.2f}{shrunk:>+13.2f}"
              f"{g['p50'].mean():>9.1f}{g['line'].mean():>9.1f}")

    if os.getenv("WRITE") == "1":
        keep = {}
        for m, b in final.items():
            sub = d[d["market_code"] == m]
            if sub["units_adj"].mean() > sub["units_base"].mean():
                keep[m] = b
        OUT.write_text(json.dumps(
            {"season": SEASON, "prior_n": PRIOR_N, "bias": keep}, indent=2),
            encoding="utf-8")
        print(f"\nwrote {OUT.name} with {len(keep)} market(s): {sorted(keep)}")
    else:
        print("\nnothing written. WRITE=1 to ship the markets that improved.")


if __name__ == "__main__":
    main()
