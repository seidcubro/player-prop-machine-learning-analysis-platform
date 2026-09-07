"""Is the quantile CDF trustworthy on the count markets?

Matthew Stafford, Week 1, a 1.5 passing-touchdown line. His last ten games were
3, 0, 3, 4, 2, 3, 2, 3, 2, 3 and the point model projects a rate of 2.61, which
matches. The quantile CDF then put P(under 1.5) at 0.53 and the board
recommended the under. A Poisson with the model's own 2.61 rate puts it at
0.265. That is a 27-point disagreement that flips the side.

Quantile regression is the wrong tool for a small integer. Touchdowns run 0 to
4, the population median is 1, and separately-fitted conditional quantiles
mostly reproduce the population shape rather than tracking a particular
quarterback's rate. The point model has no such problem: it is fitted on Poisson
deviance and its mean matches reality.

An earlier comparison found the CDF beat Poisson survival on Brier overall, and
that is the reason this was left alone. But an average hides exactly the failure
above: if the CDF is fine for the many quarterbacks near the population mean and
badly wrong for the few with high rates, the average still favours it while
every bet placed on a high-rate quarterback is on the wrong side.

So this splits the comparison by projected rate. If the CDF only loses in the
upper band, that is where the bets are and the average was the wrong summary.
"""

import os

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sqlalchemy import create_engine, text

COUNT_MARKETS = ("pass_td", "rush_td", "rec_td", "any_td")


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main():
    from backtest_season import DATABASE_URL

    engine = create_engine(DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT market_code, line, projection, win_prob, recommended_side,
               actual, hit, price_american, season
        FROM prop_edge_results
        WHERE market_code = ANY(:mk) AND hit IS NOT NULL
          AND projection IS NOT NULL AND line IS NOT NULL
    """), engine, params={"mk": list(COUNT_MARKETS)})
    if d.empty:
        raise SystemExit("no graded count-market picks")

    # Recover P(over) from the stored side-specific probability.
    d["p_over_cdf"] = np.where(d["recommended_side"] == "over",
                               d["win_prob"], 1.0 - d["win_prob"])
    # The alternative: the survival function of the model's own fitted rate.
    # Lines are half-integers, so floor(line) is the exact integer threshold and
    # no continuity correction is needed.
    d["p_over_pois"] = poisson.sf(np.floor(d["line"].to_numpy()),
                                  np.clip(d["projection"].to_numpy(), 1e-6, None))
    d["over_hit"] = (d["actual"] > d["line"]).astype(float)

    print(f"{len(d)} graded picks on {sorted(d['market_code'].unique())}")
    print()
    print("=== OVERALL, which is what the earlier comparison looked at ===")
    print(f"{'':<26}{'Brier':>10}{'log loss':>11}{'mean p':>9}{'actual':>9}")
    base = float(d["over_hit"].mean())
    print(f"{'always the base rate':<26}"
          f"{brier(np.full(len(d), base), d['over_hit']):>10.4f}"
          f"{logloss(np.full(len(d), base), d['over_hit']):>11.4f}"
          f"{base:>9.3f}{base:>9.3f}")
    print(f"{'quantile CDF (shipping)':<26}{brier(d['p_over_cdf'], d['over_hit']):>10.4f}"
          f"{logloss(d['p_over_cdf'], d['over_hit']):>11.4f}"
          f"{d['p_over_cdf'].mean():>9.3f}{base:>9.3f}")
    print(f"{'Poisson survival':<26}{brier(d['p_over_pois'], d['over_hit']):>10.4f}"
          f"{logloss(d['p_over_pois'], d['over_hit']):>11.4f}"
          f"{d['p_over_pois'].mean():>9.3f}{base:>9.3f}")

    print()
    print("=== SPLIT BY THE PROJECTED RATE, which the average hides ===")
    print("The bets land on the players with the highest rates, so a method that")
    print("is right on average and wrong at the top is wrong where it counts.")
    print()
    bands = [(0.0, 0.5, "rate < 0.5"), (0.5, 1.0, "0.5 to 1.0"),
             (1.0, 1.5, "1.0 to 1.5"), (1.5, 2.0, "1.5 to 2.0"),
             (2.0, 99.0, "rate >= 2.0")]
    print(f"{'band':<16}{'n':>6}{'actual':>9}{'CDF says':>10}{'Pois says':>11}"
          f"{'CDF Brier':>11}{'Pois Brier':>12}")
    for lo, hi, lab in bands:
        sub = d[(d["projection"] >= lo) & (d["projection"] < hi)]
        if len(sub) < 25:
            continue
        print(f"{lab:<16}{len(sub):>6}{sub['over_hit'].mean():>9.3f}"
              f"{sub['p_over_cdf'].mean():>10.3f}{sub['p_over_pois'].mean():>11.3f}"
              f"{brier(sub['p_over_cdf'], sub['over_hit']):>11.4f}"
              f"{brier(sub['p_over_pois'], sub['over_hit']):>12.4f}")

    print()
    print("=== HOW OFTEN DO THEY DISAGREE ON THE SIDE? ===")
    cdf_over = d["p_over_cdf"] > 0.5
    pois_over = d["p_over_pois"] > 0.5
    disagree = cdf_over != pois_over
    print(f"{disagree.mean():.1%} of picks ({int(disagree.sum())}) would flip side.")
    if disagree.any():
        sub = d[disagree]
        print(f"  on those, the over actually hit {sub['over_hit'].mean():.3f} "
              f"of the time")
        print(f"  the CDF wanted the over on {cdf_over[disagree].mean():.1%} of them")
        print(f"  Poisson wanted the over on {pois_over[disagree].mean():.1%}")

    print()
    print("=== BY MARKET ===")
    print(f"{'market':<12}{'n':>6}{'actual':>9}{'CDF':>9}{'Pois':>9}"
          f"{'CDF Brier':>11}{'Pois Brier':>12}")
    for mk, sub in d.groupby("market_code"):
        if len(sub) < 25:
            continue
        print(f"{mk:<12}{len(sub):>6}{sub['over_hit'].mean():>9.3f}"
              f"{sub['p_over_cdf'].mean():>9.3f}{sub['p_over_pois'].mean():>9.3f}"
              f"{brier(sub['p_over_cdf'], sub['over_hit']):>11.4f}"
              f"{brier(sub['p_over_pois'], sub['over_hit']):>12.4f}")


if __name__ == "__main__":
    main()
