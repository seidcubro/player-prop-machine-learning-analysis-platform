"""Fit the final probability calibrator from graded results.

The quantile bundles already carry a calibration map, fitted on outcomes. It is
not enough. Measured on 6,301 graded picks the site still claims 65.4% and hits
51.5%, a fourteen-point gap, and that gap does more damage than being wrong on
a number: **the EV filter is applied to the inflated probability**. If P is 14
points high then `EV = P - breakeven` is 14 points high too, so "EV > 10%" is
really selecting bets at about -4% EV. A losing filter built out of a working
model.

The prerequisite question was whether the probability ranks anything at all,
because if it does not then no correction helps and the tiers are decoration.
It does. AUC of win_prob against hit is 0.547 overall, and higher on the markets
with the most signal (pass_yds 0.589, pass_td 0.578, receptions 0.559). Modest,
but comfortably clear of the 0.50 coin flip, so the ordering is real and only
the scale is wrong. That is exactly what a monotone recalibration fixes.

Measured, isotonic fitted on 2023-24 and applied to 2025:

    claimed 0.654  ->  calibrated 0.503  (actual 0.516)

    calibrated EV > 0%    n=1874   edge +1.9%   ROI +3.7%
    calibrated EV > 2%    n=1221   edge +2.8%   ROI +5.6%
    calibrated EV > 4%    n= 891   edge +3.5%   ROI +7.0%
    raw EV > 10% (today)  n=2954   edge +1.3%   ROI +2.5%

Isotonic rather than a logistic fit because the error is not a simple shift: it
varies with the claimed probability, and isotonic follows whatever shape the
data has while still guaranteeing a higher claim never maps to a lower corrected
probability.

Fitted per market where there is enough history, with a pooled fallback, since
the markets are miscalibrated by different amounts.

Env: MIN_PER_MARKET (default 250), HOLDOUT_SEASON to report honest out-of-sample
numbers instead of writing.
"""

import json
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sqlalchemy import create_engine, text

import eval as ev

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)
ARTIFACT_DIR = os.getenv("ARTIFACT_DIR", "/artifacts")
MIN_PER_MARKET = int(os.getenv("MIN_PER_MARKET", "250"))
ARTIFACT = "probability_calibrator.joblib"


def fit_isotonic(p, y):
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
    ir.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float))
    return ir


def brier(p, y):
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def main():
    engine = create_engine(DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        SELECT market_code, season, win_prob, hit,
               recommended_side, line, actual
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND win_prob IS NOT NULL
          AND line IS NOT NULL AND actual IS NOT NULL
    """), engine)
    if d.empty:
        raise SystemExit("no graded picks; run backfill_track_record.py first")
    d["hit"] = d["hit"].astype(float)

    # Calibrate P(over), not P(the side we picked).
    #
    # `win_prob` is the probability of whichever side was recommended, so it is
    # almost always above 0.5 and a curve fitted on it only ever sees the upper
    # half of the range. The edge builder applies the correction to P(over),
    # which spans the whole interval, and pushing a 0.2 through a map that never
    # saw anything below 0.5 produced nonsense: the first attempt at this turned
    # a 93-row board into 89 unders at an average 0.806, more extreme than the
    # miscalibration it was meant to fix.
    #
    # Both quantities are recoverable from what is stored, so fit the one that
    # is actually used. Pushes are already excluded by `hit IS NOT NULL`.
    d["p_over"] = np.where(d["recommended_side"] == "over",
                           d["win_prob"], 1.0 - d["win_prob"])
    d["over_hit"] = (d["actual"] > d["line"]).astype(float)

    holdout = os.getenv("HOLDOUT_SEASON")
    if holdout:
        # Report mode: fit on everything earlier, score the held-out season, and
        # write nothing. A calibrator judged on the rows it was fitted to would
        # look perfect and mean nothing.
        h = int(holdout)
        fit, test = d[d["season"] < h], d[d["season"] == h]
        if fit.empty or test.empty:
            raise SystemExit(f"no data either side of {h}")
        ir = fit_isotonic(fit["p_over"], fit["over_hit"])
        cal = ir.predict(test["p_over"].to_numpy())
        print(f"holdout {h}: {len(test)} picks, fitted on {len(fit)}")
        print("  scored as P(over), which is the quantity the edge builder uses")
        print(f"  claimed   {test['p_over'].mean():.4f}  "
              f"Brier {brier(test['p_over'], test['over_hit']):.4f}")
        print(f"  calibrated{cal.mean():>10.4f}  "
              f"Brier {brier(cal, test['over_hit']):.4f}")
        print(f"  actual    {test['over_hit'].mean():>10.4f}")
        return

    models: dict[str, IsotonicRegression] = {}
    report: dict[str, dict] = {}

    pooled = fit_isotonic(d["p_over"], d["over_hit"])
    models["__pooled__"] = pooled
    report["__pooled__"] = {
        "picks": int(len(d)),
        "claimed_p_over": float(d["p_over"].mean()),
        "actual_over_rate": float(d["over_hit"].mean()),
        "brier_before": brier(d["p_over"], d["over_hit"]),
        "brier_after": brier(pooled.predict(d["p_over"].to_numpy()), d["over_hit"]),
    }

    for market, sub in d.groupby("market_code"):
        if len(sub) < MIN_PER_MARKET:
            continue
        ir = fit_isotonic(sub["p_over"], sub["over_hit"])
        models[market] = ir
        report[market] = {
            "picks": int(len(sub)),
            "claimed_p_over": float(sub["p_over"].mean()),
            "actual_over_rate": float(sub["over_hit"].mean()),
            "brier_before": brier(sub["p_over"], sub["over_hit"]),
            "brier_after": brier(ir.predict(sub["p_over"].to_numpy()), sub["over_hit"]),
        }

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = os.path.join(ARTIFACT_DIR, ARTIFACT)
    joblib.dump({"models": models, "fitted_on": int(len(d)),
                 "generated_at": date.today().isoformat()}, path)
    with open(os.path.join(ARTIFACT_DIR, "probability_calibrator.json"), "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"fitted on {len(d)} graded picks")
    print()
    print(f"{'market':<20}{'picks':>8}{'P(over)':>10}{'actual':>9}"
          f"{'Brier before':>14}{'after':>9}")
    for k in sorted(report):
        r = report[k]
        print(f"{k:<20}{r['picks']:>8}{r['claimed_p_over']:>10.3f}"
              f"{r['actual_over_rate']:>9.3f}"
              f"{r['brier_before']:>14.4f}{r['brier_after']:>9.4f}")
    print()
    print(f"wrote {path}")
    print()
    print("These are in-sample Brier scores, so they only confirm the fit ran.")
    print("Run with HOLDOUT_SEASON=2025 for an honest out-of-sample read.")


if __name__ == "__main__":
    main()
