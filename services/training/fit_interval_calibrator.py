"""Make the published range mean what it says, on the players who get priced.

Measured on the rows a sportsbook actually posted a line for, the intervals are
wrong in a consistent direction:

                below p10   below p25   below p50   above p90
    should be        0.10        0.25        0.50        0.10
    recs             0.060       0.226       0.466       0.130
    rec_yds          0.055       0.111       0.439       0.130
    rush_yds         0.061       0.190       0.460       0.125

The band is too wide at the bottom, too narrow at the top, and the median sits
near the 44th percentile rather than the 50th. A reader looking at "20 to 108"
is being told the player clears 108 one game in ten when he really does it one
game in eight.

train_quantiles already fits a probability-integral-transform map, and already
fits it on priced players rather than a snap-share proxy. What it cannot do is
filter to priced player-GAMES: it selects every game by any player a book has
ever posted this market on, which sweeps in the weeks they were hurt, benched or
barely used. Those games sit low, and they are what stretches the lower tail.

prop_edge_results is that missing population exactly: one row per player-game a
line was posted on, with the outcome attached. This fits a second map there and
composes it with the first.

The ladder is read from that table and not from player_projection_history.
History is written by the shipped model, which train.py refits on every row it
has, so a correction fitted there is fitted on predictions of games the model
was trained on. The first version of this file did exactly that and the numbers
above are what it saw. The same mistake on the spread calibrator produced a
correction that stretched predictions where walk-forward data said to shrink
them by half, which is how it was found. These quantiles come from models refit
on seasons strictly before the one they predict, and they are stored the way the
site publishes them, read through the calibration map rather than raw.

On that honest ladder the picture is smaller but real, and the direction is
consistent: outcomes clear the published p90 between 11% and 18% of the time in
every market rather than 10%. The ceiling is too low everywhere, which is the
side of the interval that matters, because it is the one a reader treats as the
realistic best case and the one an over has to get through.

Written per market only where the holdout coverage actually improves, scored by
mean absolute deviation from the levels each quantile claims to be.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from interval_calibration import _read_ladder
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
OUT = ARTIFACTS / "interval_calibrator.json"

LEVELS = [0.10, 0.25, 0.50, 0.75, 0.90]
MIN_ROWS = 300
# Smallest holdout improvement worth shipping. A level can clear the per-level
# test on a fraction of a percentage point, which is not a correction, it is the
# holdout season being one sample of many. recs came through the honest refit at
# a 0.9% gain off a single level; rec_yds came through at 32.9% off three.
MIN_GAIN = float(os.getenv("MIN_INTERVAL_GAIN", "0.05"))
# Share of rows, oldest first, used to fit. The rest is the holdout.
FIT_FRACTION = float(os.getenv("FIT_FRACTION", "0.70"))


def coverage(df: pd.DataFrame) -> dict:
    """Share of outcomes at or below each published quantile."""
    return {q: float((df["actual"] < df[f"p{int(q * 100)}"]).mean())
            for q in LEVELS}


def miscalibration(cov: dict) -> float:
    """Mean absolute distance from the level each quantile claims to be."""
    return float(np.mean([abs(cov[q] - q) for q in LEVELS]))


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(text("""
        SELECT r.market_code, r.game_date, r.actual,
               r.q10 AS p10, r.q25 AS p25, r.projection_median AS p50,
               r.q75 AS p75, r.q90 AS p90
        FROM prop_edge_results r
        WHERE r.actual IS NOT NULL AND r.q10 IS NOT NULL AND r.q90 IS NOT NULL
          AND r.projection_median IS NOT NULL
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
        print("no walk-forward rows with stored quantiles yet; nothing to fit. "
              "Run backfill_track_record.py, which computes them.")
        OUT.write_text("{}", encoding="utf-8")
        return

    d = pd.to_datetime(df["game_date"])
    df["season"] = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
    # Split by date, not by season.
    #
    # Holding out a whole season sounds cleaner and starves the fit. The odds
    # history is lopsided: of 6,826 walk-forward rows, 5,532 are 2025 and the
    # rest are spread over two earlier seasons. Fitting on "everything before
    # 2025" left 66 to 354 rows per market against a 300 row minimum, so five
    # markets of six could not be fitted at all and shipped uncorrected. Those
    # are the ones whose published medians run 0.39 to 0.47 against a nominal
    # 0.50, which is what makes almost every published pick an under.
    #
    # A date cut keeps the split walk-forward, which is the property that
    # matters, and puts the bulk of the rows where they do some good.
    df = df.assign(_d=d).sort_values("_d").reset_index(drop=True)
    cut_at = df["_d"].quantile(FIT_FRACTION)
    df["_fit"] = df["_d"] <= cut_at

    print(f"{len(df)} priced rows, fitting on games up to {cut_at.date()}, "
          f"scoring on everything after")
    print(f"{'market':<14}{'n fit':>7}{'n test':>7}"
          f"{'miscal raw':>12}{'miscal adj':>12}{'gain':>8}  verdict")

    out = {}
    for market, g in df.groupby("market_code"):
        fit = g[g["_fit"]]
        test = g[~g["_fit"]]
        if len(fit) < MIN_ROWS or len(test) < 150:
            print(f"{market:<14}{len(fit):>7}{len(test):>7}"
                  f"{'-':>12}{'-':>12}{'-':>8}  too few rows")
            continue

        # Where each outcome actually falls inside its own published ladder.
        # The distribution of those positions is the correction: if outcomes
        # land below the p10 only 6% of the time, then what is being published
        # as a p10 is really a p06, and the level has to move.
        ladder = np.sort(fit[[f"p{int(q * 100)}" for q in LEVELS]].to_numpy(), axis=1)
        # Extrapolated, for the same reason the inversion is.
        #
        # np.interp pins every outcome above a row's p90 to exactly 0.90, so
        # the share at or below the p90 comes out as 1.000 by construction and
        # the map is told the ceiling is never cleared. It is cleared 11% to
        # 18% of the time. Continuing each row's ladder at the slope of its
        # last segment lets those outcomes sit above 0.90 where they belong,
        # which is the only way the map can learn that the top is too low.
        pos = np.clip([
            _read_ladder(a, list(row), LEVELS)
            for a, row in zip(fit["actual"].to_numpy(dtype=float), ladder)
        ], 0.0, 1.0)
        # Empirical level reached at each published level, monotone by
        # construction since it is a sorted quantile of the positions.
        mapped = [float(np.mean(pos <= q)) for q in LEVELS]

        # Invert: to publish a true q, read the ladder where the empirical map
        # says q is reached.
        #
        # Extrapolated past the ends rather than clamped, and clamped only at
        # 0.01 and 0.99 to keep the result a probability. np.interp stops at
        # 0.90, which is the exact level that needs to move: outcomes clear the
        # published p90 more than a tenth of the time in every market, so an
        # honest p90 lives above level 0.90 and a clamped inversion leaves the
        # one broken level untouched while dutifully correcting the middle.
        read_at = [min(0.99, max(0.01, _read_ladder(q, mapped, LEVELS)))
                   for q in LEVELS]

        test_ladder = np.sort(
            test[[f"p{int(q * 100)}" for q in LEVELS]].to_numpy(), axis=1)
        # Read exactly the way interval_calibration.apply reads, or the
        # holdout score describes a correction that is not the one shipped.
        adj = pd.DataFrame({
            f"p{int(q * 100)}": [max(0.0, _read_ladder(r, LEVELS, list(row)))
                                 for row in test_ladder]
            for q, r in zip(LEVELS, read_at)
        })
        adj["actual"] = test["actual"].to_numpy()

        # Accepted level by level, never as a package.
        #
        # The first version took the market as a whole on its average
        # miscalibration, and that average hid a regression: on rec_yds the p25
        # went from 0.106 to a clean 0.250 while the p90 slid from 0.875 to
        # 0.842, away from its target. The aggregate still looked like a 47%
        # improvement. The p90 is the number a reader treats as the ceiling, so
        # trading it for a better middle is not a trade worth making.
        #
        # A level that does not improve keeps its published value, which is why
        # read_at falls back to the level itself rather than being dropped.
        raw_cov = coverage(test)
        adj_cov = coverage(adj)
        final_read = []
        kept_levels = []
        for i, q in enumerate(LEVELS):
            better = abs(adj_cov[q] - q) < abs(raw_cov[q] - q) - 0.002
            final_read.append(read_at[i] if better else q)
            if better:
                kept_levels.append(f"p{int(q * 100)}")

        if not kept_levels:
            print(f"{market:<14}{len(fit):>7}{len(test):>7}"
                  f"{miscalibration(raw_cov):>12.4f}{'-':>12}{'-':>8}  no level improved")
            continue

        final = pd.DataFrame({
            f"p{int(q * 100)}": [max(0.0, _read_ladder(r, LEVELS, list(row)))
                                 for row in test_ladder]
            for q, r in zip(LEVELS, final_read)
        })
        final["actual"] = test["actual"].to_numpy()
        raw_m = miscalibration(raw_cov)
        adj_m = miscalibration(coverage(final))
        gain = (raw_m - adj_m) / raw_m if raw_m else 0.0
        if gain < MIN_GAIN:
            print(f"{market:<14}{len(fit):>7}{len(test):>7}"
                  f"{raw_m:>12.4f}{adj_m:>12.4f}{gain:>7.1%}  "
                  f"gain under {MIN_GAIN:.0%}, not shipped")
            continue
        print(f"{market:<14}{len(fit):>7}{len(test):>7}"
              f"{raw_m:>12.4f}{adj_m:>12.4f}{gain:>7.1%}  "
              f"corrects {', '.join(kept_levels)}")
        out[market] = {"levels": LEVELS, "read_at": final_read,
                       "corrected_levels": kept_levels,
                       "fitted_rows": int(len(fit)),
                       "holdout_after": str(cut_at.date()),
                       "holdout_gain": float(gain)}

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT.name} with {len(out)} market(s)")


if __name__ == "__main__":
    main()
