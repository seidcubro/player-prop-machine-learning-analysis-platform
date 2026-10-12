"""Undo the flattening in the point predictions, per market.

The models are unbiased on the average player and wrong on everyone else. For
receptions the predictions average 2.29 against an actual 2.30, and vary with a
standard deviation of 1.50 against an actual 2.31. Trey McBride's week 1 row is
what that looks like on one player: every feature said about 7.7 catches, the
model said 5.7, he caught 9.

Squared-error training with regularisation does this on purpose. Pulling a
prediction toward the mean is the safe way to lower average error, and it is the
wrong trade for betting, because a sportsbook posts lines on exactly the players
a flattened model reads worst. Being low on every star is how a board ends up
93% unders and hits 47.5%.

The correction is the textbook one, a linear recalibration of the prediction:

    adjusted = a + b * predicted

fitted by least squares of the outcome on the prediction. b above 1 stretches
the predictions away from the mean, which is what a compressed model needs.

Fitted per market on earlier seasons and scored on the most recent full season,
never on the rows it was fitted to. Written only for markets where it actually
helps out of sample; a market that does not improve keeps its raw prediction and
is reported as skipped. The noise rule applies here as everywhere else in this
project: a change that cannot show an out-of-sample gain does not ship.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

# Built from POSTGRES_* the way every other script here does, with an explicit
# DATABASE_URL still winning if one is set.
#
# The dev fallback this used to carry was postgresql://app:app@postgres, which
# is the development password and is correct only by coincidence: the dev stack
# happens to use it. Production sets POSTGRES_PASSWORD to a random string and
# does not set DATABASE_URL at all, so anything relying on that fallback would
# have failed on the server with an authentication error. Two of these scripts
# run in the weekly pipeline.
DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)
ARTIFACTS = Path(os.getenv("ARTIFACT_DIR", "/artifacts"))
OUT = ARTIFACTS / "spread_calibrator.json"

# Every market reads its outcome from the same column now: prop_edge_results
# stores the graded result beside the pick, so there is nothing to map.
STAT = {m: "actual" for m in
        ("recs", "rec_yds", "rush_att", "rush_yds", "pass_att",
         "pass_completions", "pass_yds", "pass_td", "any_td")}

MIN_GAIN = 0.002   # 0.2% of MAE, below this is noise

# Split chronologically, not by season.
#
# The season holdout could not be made to work here, and the reason is the odds
# archive rather than this script: prop_edge_results is effectively one full
# season of history. Holding out the newest season with "enough" rows picked
# 2026 in week five, so every market had a few hundred rows to score against
# and the whole file came back empty:
#
#   rec_yds   2697 to fit,  331 to score, need 400 -> skipped
#   recs      2618 to fit,  354 to score, need 400 -> skipped
#   rush_att  1067 to fit,  143 to score, need 400 -> skipped
#   pass_att     0 to fit,   77 to score, need 400 -> skipped
#
# Nothing had failed. The gate was asking a third of a season to look like a
# whole one, and meanwhile the board under-projects passing yards by 12 and
# receiving yards by 3 every week, which is the exact bias this corrects. The
# audit reported "corrects nothing" as though that were a measurement.
#
# Moving the holdout back to 2025 does not fix it, it moves the hole: the mass
# of the archive IS 2025, so fitting on what comes before leaves the thin
# markets with nothing to fit on.
#
# A chronological split has neither problem. The map being fitted is a monotone
# one-dimensional function from prediction to outcome, so a season boundary
# carries no special meaning for it, and training strictly before a date and
# scoring strictly after is the out-of-sample discipline that actually matters.
SPLIT_FRACTION = float(os.getenv("SPREAD_SPLIT_FRACTION", "0.70"))

# Asymmetric on purpose. The fit needs enough rows to place 24 isotonic knots;
# the holdout only needs enough to measure a mean absolute error, and demanding
# the same count of both is what left four markets unmeasurable.
MIN_FIT_ROWS = 400
MIN_TEST_ROWS = 250



def _isotonic(x: np.ndarray, y: np.ndarray, points: int = 24) -> dict:
    """A monotone lookup from prediction to expected outcome.

    Isotonic rather than a straight line because the error is not a constant
    scaling: the model is right for low usage players and increasingly low for
    high usage ones. A line has to average those two regimes and ends up fixing
    neither. Monotone because a higher projection must never map to a lower
    corrected one, which a free-form fit on thin tail data would otherwise do.
    """
    from sklearn.isotonic import IsotonicRegression

    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(x, y)

    # The grid stops at the 99th percentile, not the maximum.
    #
    # Isotonic regression fits the last block exactly, and the last block at the
    # very top is one row. Reading the map to the maximum produced endpoints set
    # by a single game: rec_yds mapped a prediction of 107 to 227 yards, pass_td
    # mapped 2.83 to five touchdowns, pass_yds mapped 316 to 494. Everything
    # above then clamped onto those, so the correction turned the biggest
    # projections into nonsense, which is the exact opposite of the intent.
    #
    # Ending at the 99th percentile puts the last point somewhere with enough
    # rows behind it to mean something, and np.interp clamps beyond it to that
    # value rather than to an outlier.
    lo, hi = np.quantile(x, 0.01), np.quantile(x, 0.99)
    grid = np.unique(np.quantile(x[(x >= lo) & (x <= hi)],
                                 np.linspace(0, 1, points)))
    return {"x": grid, "y": ir.predict(grid)}


def season_of(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(text("""
        -- Walk-forward predictions, from prop_edge_results, never the
        -- projection history.
        --
        -- This fitted on player_projection_history, which backfill_projection_
        -- history fills by running the ACTIVE model over past games. train.py
        -- refits the shipped model on every row it has, so those predictions
        -- have already seen the seasons this then holds out. Fitting a
        -- correction on them measures how a model fits its own training data.
        --
        -- The two disagree about the direction of the correction, not just its
        -- size. Regressing outcome on prediction gives a slope of 1.359 for
        -- pass_td in-sample and 0.531 walk-forward; 1.050 against 0.484 for
        -- pass_yds. In-sample says stretch these predictions away from the
        -- mean, honest data says shrink them toward it by half. The first
        -- version of this shipped the stretch.
        --
        -- prop_edge_results is built by backfill_track_record, which refits the
        -- point model, the quantiles and the calibration on seasons strictly
        -- before the one it scores. Its projections are what this model would
        -- have said at the time, which is the only thing worth correcting.
        --
        -- It also covers only priced player-games, which is the population that
        -- gets bet, so the correction is measured where it is applied.
        --
        -- build_projections applies this map and then writes the result into
        -- the same history this reads, so fitting on `projection` would learn a
        -- correction on top of last week's correction and compound all season.
        -- COALESCE covers rows written before the raw columns existed, which
        -- came from the backfill and were never corrected.
        SELECT r.market_code, r.game_date,
               r.projection, r.projection_median AS p50,
               r.actual
        FROM prop_edge_results r
        WHERE r.actual IS NOT NULL AND r.projection IS NOT NULL
    """), eng)
    # Nothing to fit is not a failure.
    #
    # These corrections are optional by design: a market that does not improve
    # ships uncorrected, and both builders handle a missing artifact by leaving
    # predictions alone. A brand new server has no projection history at all
    # until the first build_projections run writes some, and this step runs
    # before that one in scheduled_update.sh. Exiting non-zero here would abort
    # the whole weekly pipeline on its first run, on a database that is simply
    # new rather than broken.
    #
    # An empty artifact is written so the state is explicit rather than absent,
    # and audit check [17] still reports which corrections are active.
    if df.empty:
        print("no projection history yet; nothing to fit")
        OUT.write_text("{}", encoding="utf-8")
        return

    df["season"] = season_of(df["game_date"])
    df["game_date"] = pd.to_datetime(df["game_date"])

    # The cut is a date taken per market below, so one game-day never lands on
    # both sides of it.
    print(f"{len(df):,} graded rows, {df.game_date.min().date()} to "
          f"{df.game_date.max().date()}")
    print(f"fitting on the earliest {SPLIT_FRACTION:.0%} of each market's rows "
          f"by date, scoring on the rest")
    print()
    print(f"{'market':<18}{'n fit':>7}{'n test':>8}{'from':>12}"
          f"{'pt gain':>9}{'pt top10%':>11}{'med gain':>9}{'med top10%':>11}  keeps")

    out = {}
    for market, g in df.groupby("market_code"):
        c = STAT.get(market)
        if not c or c not in g:
            continue
        g = g.dropna(subset=["projection", c]).sort_values("game_date")
        cut = g["game_date"].quantile(SPLIT_FRACTION)
        fit = g[g["game_date"] < cut]
        test = g[g["game_date"] >= cut]
        when = str(pd.Timestamp(cut).date()) if len(g) else "-"
        if len(fit) < MIN_FIT_ROWS or len(test) < MIN_TEST_ROWS:
            # The fit count and the test count, in their own columns. These
            # were printed into one "n test" column, so rec_yds read 1667
            # against recs reading 354 and 1762 and the two were not the same
            # quantity.
            print(f"{market:<18}{len(fit):>7}{len(test):>8}{when:>12}"
                  f"{'-':>9}{'-':>11}{'-':>9}{'-':>11}  too few rows "
                  f"(need {MIN_FIT_ROWS} to fit, {MIN_TEST_ROWS} to score)")
            continue

        entry = {}
        cells = []
        # The point projection and the median are fitted and judged separately.
        #
        # The board picks a side from the median, not the mean, so correcting
        # only the point prediction would improve the projections page and not
        # change a single bet. They are also compressed by different amounts,
        # because the median comes through the quantile ensemble and its own
        # calibration map.
        for field, key in (("projection", "point"), ("p50", "median")):
            f = fit.dropna(subset=[field])
            t = test.dropna(subset=[field])
            if (len(f) < MIN_FIT_ROWS or len(t) < MIN_TEST_ROWS
                    or f[field].std() == 0):
                cells.append(("-", "-", "-", "-"))
                continue
            b, a = np.polyfit(f[field].to_numpy(dtype=float),
                              f[c].to_numpy(dtype=float), 1)
            p = t[field].to_numpy(dtype=float)
            y = t[c].to_numpy(dtype=float)
            adj = np.clip(a + b * p, 0, None)
            iso = _isotonic(f[field].to_numpy(dtype=float),
                            f[c].to_numpy(dtype=float))
            adj_iso = np.clip(np.interp(p, iso["x"], iso["y"]), 0, None)
            mae_raw = float(np.mean(np.abs(p - y)))
            mae_adj = float(np.mean(np.abs(adj - y)))
            mae_iso = float(np.mean(np.abs(adj_iso - y)))

            # Judged on the rows that get bet, not on the whole population.
            #
            # A straight line through every row says receptions need no
            # correction, because 95% of rows are low usage players the model
            # already reads correctly. The bias lives in the top few buckets:
            # at a five game average of seven to eight catches the model comes
            # in 0.30 low, and that is precisely the population a sportsbook
            # posts lines on. A global average hides it by drowning it.
            hi = p >= np.quantile(p, 0.90)
            gain = (mae_raw - mae_iso) / mae_raw
            gain_hi = ((np.mean(np.abs(p[hi] - y[hi]))
                        - np.mean(np.abs(adj_iso[hi] - y[hi])))
                       / np.mean(np.abs(p[hi] - y[hi]))) if hi.sum() else 0.0
            cells.append((f"{b:.2f}", f"{gain:.1%}", f"{gain_hi:.1%}",
                          f"{mae_adj:.3f}"))
            if gain > MIN_GAIN or gain_hi > MIN_GAIN * 5:
                entry[key] = {"kind": "isotonic",
                              "x": [float(v) for v in iso["x"]],
                              "y": [float(v) for v in iso["y"]],
                              "fitted_rows": int(len(f)),
                              "holdout_gain": float(gain),
                              "holdout_gain_top_decile": float(gain_hi)}

        verdict = "+".join(sorted(k for k in entry)) if entry else "no gain"
        pt, md = cells[0], cells[1]
        print(f"{market:<18}{len(fit):>7}{len(test):>8}{when:>12}"
              f"{pt[1]:>9}{pt[2]:>11}{md[1]:>9}{md[2]:>11}  {verdict}")
        if entry:
            entry["holdout_from"] = when
            entry["holdout_rows"] = int(len(test))
            out[market] = entry

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT.name} with {len(out)} market(s)")
    if not out:
        print("nothing improved out of sample; predictions ship unchanged")


if __name__ == "__main__":
    main()
