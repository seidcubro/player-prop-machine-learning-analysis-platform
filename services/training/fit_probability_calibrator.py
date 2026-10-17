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


# How fast an old season stops counting.
#
# The map was fitted pooled across every graded row with equal weight, and
# production falls about 3% a player-game a year, so a curve built mostly from
# 2023-2025 reads high all through 2026. Measured on the 2026 holdout, fitted
# on the 8,548 rows before it:
#
#   claimed     0.4062   Brier 0.2738
#   calibrated  0.4687   Brier 0.2519
#   actual      0.5192
#
# The map closes about half the gap and leaves five points. Five points is
# what decides a bet at -110, so every expected value on the board is inflated
# by roughly that much and the filter selects negatives. The board does not
# believe in overs: it says they happen 40.6% of the time and they happen
# 51.9% of the time.
#
# A half-life of one season halves the weight of each season you go back, so
# the most recent complete season carries about as much as everything before
# it combined. Set CAL_HALF_LIFE=0 to restore the old equal-weight fit; the
# report mode below prints both so the choice is made on the holdout and not
# on an argument.
CAL_HALF_LIFE = float(os.getenv("CAL_HALF_LIFE", "1.0"))


def season_weights(season, half_life=None):
    """Exponential decay in seasons, newest season weight 1.0."""
    hl = CAL_HALF_LIFE if half_life is None else half_life
    s = np.asarray(season, dtype=float)
    if hl <= 0:
        return np.ones_like(s)
    return np.power(0.5, (np.nanmax(s) - s) / hl)


def fit_isotonic(p, y, w=None):
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99)
    ir.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float),
           sample_weight=None if w is None else np.asarray(w, dtype=float))
    return ir


def brier(p, y):
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))



# How much of the current season's own level to believe.
#
# The pooled map gets the SHAPE right and the LEVEL wrong. Measured on the 2026
# holdout it moves P(over) from 0.4062 to 0.4687 against an actual 0.5192, so it
# closes about half the gap and leaves five points, and the live board then
# claims 0.558 while hitting 0.487. Recency weighting across past seasons does
# not reach it either: no past season ran as hot as this one and you cannot
# reweight your way to a level you have never seen.
#
# So the level comes from the current season itself, as ONE number rather than a
# second curve. With 1,296 live rows an isotonic map would chase noise; a single
# offset is one parameter and is safe at that size.
#
#     offset = (actual over rate - calibrated over rate) * n / (n + PRIOR)
#
# Shrunk so week two cannot swing it. Written only if a walk-forward check says
# it helps: for each game date, the offset is fitted on live rows strictly
# before it and scored on that date, which is the only honest way to ask whether
# using it would have helped rather than whether it describes what happened.
SEASON_OFFSET_PRIOR = float(os.getenv("SEASON_OFFSET_PRIOR", "300"))
CURRENT_SEASON = int(os.getenv("CURRENT_SEASON", "2026"))


def fit_season_offset(d, models):
    """One scalar for this season's level, with a walk-forward verdict."""
    live = d[d["season"] == CURRENT_SEASON]
    if "source" in live.columns:
        live = live[live["source"] == "live"]
    if len(live) < 200:
        print(f"season offset: only {len(live)} current-season rows, skipped")
        return None

    def calibrated(rows):
        out = np.empty(len(rows))
        for i, (m, p) in enumerate(zip(rows["market_code"], rows["p_over"])):
            ir = models.get(m) or models.get("__pooled__")
            out[i] = float(ir.predict(np.asarray([p], dtype=float))[0])
        return out

    live = live.sort_values("game_date")
    dates = sorted(live["game_date"].unique())
    before = []
    after = []
    for dt in dates:
        past, now = live[live["game_date"] < dt], live[live["game_date"] == dt]
        if len(now) == 0:
            continue
        cal_now = calibrated(now)
        before.append(np.abs(cal_now.mean() - now["over_hit"].mean()) * len(now))
        if len(past) < 100:
            after.append(before[-1])
            continue
        cal_past = calibrated(past)
        off = ((past["over_hit"].mean() - cal_past.mean())
               * len(past) / (len(past) + SEASON_OFFSET_PRIOR))
        after.append(np.abs((cal_now + off).mean() - now["over_hit"].mean()) * len(now))

    n = len(live)
    err_before, err_after = sum(before) / n, sum(after) / n
    cal_all = calibrated(live)
    offset = float((live["over_hit"].mean() - cal_all.mean())
                   * n / (n + SEASON_OFFSET_PRIOR))
    print(f"season offset: {n} live {CURRENT_SEASON} rows, "
          f"calibrated {cal_all.mean():.4f} against actual "
          f"{live['over_hit'].mean():.4f}")
    print(f"  walk-forward mean absolute level error "
          f"{err_before:.4f} -> {err_after:.4f}")
    if err_after >= err_before:
        print("  it does not help week to week, so nothing is written")
        return None
    print(f"  shipping offset {offset:+.4f}")
    return offset


def main():
    engine = create_engine(DATABASE_URL, future=True)
    d = pd.read_sql(text("""
        -- The uncorrected probability, never this map's own output.
        --
        -- build_prop_edges applies the map and stores the result as win_prob,
        -- which grade_edges copies here. Fitting on that column means fitting
        -- on corrected numbers: if the map is working they look calibrated, the
        -- refit learns the identity, and applying the identity takes the
        -- correction away again. Backtested rows have no raw column because
        -- they never went through this map, so their win_prob is already raw.
        SELECT market_code, season, game_date, source,
               COALESCE(win_prob_raw, win_prob) AS win_prob, hit,
               recommended_side, line, actual
        FROM prop_edge_results
        WHERE hit IS NOT NULL AND COALESCE(win_prob_raw, win_prob) IS NOT NULL
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
        print(f"holdout {h}: {len(test)} picks, fitted on {len(fit)}")
        print("  scored as P(over), which is the quantity the edge builder uses")
        print(f"  claimed   {test['p_over'].mean():>10.4f}  "
              f"Brier {brier(test['p_over'], test['over_hit']):.4f}")
        # Both fits, same rows, same holdout. The noise rule decides.
        for label, hl in (("equal weight", 0.0), (f"half-life {CAL_HALF_LIFE:g}", CAL_HALF_LIFE)):
            w = season_weights(fit["season"], hl)
            ir = fit_isotonic(fit["p_over"], fit["over_hit"], w)
            cal = ir.predict(test["p_over"].to_numpy())
            print(f"  {label:<16}{cal.mean():>10.4f}  "
                  f"Brier {brier(cal, test['over_hit']):.4f}  "
                  f"still {test['over_hit'].mean() - cal.mean():+.4f} from actual")
        print(f"  actual    {test['over_hit'].mean():>10.4f}")
        return

    models: dict[str, IsotonicRegression] = {}
    report: dict[str, dict] = {}

    pooled = fit_isotonic(d["p_over"], d["over_hit"], season_weights(d["season"]))
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
        ir = fit_isotonic(sub["p_over"], sub["over_hit"],
                          season_weights(sub["season"]))
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
    offset = fit_season_offset(d, models)
    joblib.dump({"models": models, "fitted_on": int(len(d)),
                 "season_offset": offset,
                 "season_offset_for": CURRENT_SEASON,
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
