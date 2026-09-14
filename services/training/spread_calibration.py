"""Apply the fitted level-dependent correction to a prediction.

The models are calibrated for the low-usage rows that make up most of the data
and read high-usage players differently, which is the population sportsbooks
post lines on. fit_spread_calibrator.py measures that per market on earlier
seasons and validates on a held-out one; this applies what it found.

Two separate corrections, because they are compressed by different amounts and
one is not a substitute for the other:

  point   the mean projection, which the projections page leads with
  median  the p50, which the board uses to pick a side

A market appears here only if its correction beat the raw prediction out of
sample. Everything else passes through untouched, which is the default and the
safe direction: a missing entry means the raw number was already as good.
"""

import json
from pathlib import Path

import numpy as np

_CACHE: dict | None = None


def load(artifact_dir: Path) -> dict:
    """Read the calibrator, once. Absent file means no corrections."""
    global _CACHE
    if _CACHE is None:
        path = Path(artifact_dir) / "spread_calibrator.json"
        if path.exists():
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
            kept = sorted(_CACHE)
            print(f"  spread calibrator: {len(kept)} market(s) corrected "
                  f"({', '.join(kept)})")
        else:
            _CACHE = {}
            print("  no spread calibrator found; predictions ship uncorrected")
    return _CACHE


def apply(bundle: dict, market_code: str, kind: str, value: float) -> float:
    """Map one prediction through its market's correction.

    `kind` is "point" or "median". Values outside the fitted range clamp to the
    ends rather than extrapolating: the correction was measured between the
    smallest and largest prediction the model made on four seasons, and beyond
    that there is nothing to stand on.
    """
    if value is None or not np.isfinite(value):
        return value
    entry = (bundle or {}).get(market_code, {}).get(kind)
    if not entry:
        return float(value)
    x = np.asarray(entry["x"], dtype=float)
    y = np.asarray(entry["y"], dtype=float)
    if x.size < 2:
        return float(value)

    v = float(value)
    if v > x[-1]:
        # Past the fitted range the correction stops, it does not guess.
        #
        # This region is not hypothetical: the grid for pass_td ends at 2.31 and
        # the board carries projections up to 3.97, so the biggest numbers on
        # the site are the ones outside it. Two earlier attempts were both
        # wrong. Clamping to the last fitted value pulled a 400 yard projection
        # down below a 300 yard one. Carrying the last ratio pushed 3.97
        # touchdowns to 5.08.
        #
        # Taking the larger of the raw value and the last fitted output keeps
        # the map monotone and cannot inflate: beyond the data the number is the
        # model's own, uncorrected, which is the honest answer when there is
        # nothing to correct it with.
        out = max(v, float(y[-1]))
    elif v < x[0]:
        out = min(v, float(y[0]))
    else:
        out = float(np.interp(v, x, y))

    # A correction, not a rewrite.
    #
    # The map is fitted on four seasons and read on whatever today's model
    # produces, and the two do not have to overlap. A prediction past the end of
    # the grid clamps to the last point, which is right, but a fit that goes
    # wrong at the edges should not be able to double a number on its own. This
    # bounds the adjustment to a range wider than any validated correction and
    # narrower than an absurd one, so a bad fit degrades to roughly the raw
    # prediction instead of to a five touchdown quarterback.
    return float(max(0.0, min(max(out, v * 0.70), v * 1.45)))
