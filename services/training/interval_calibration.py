"""Apply the fitted interval correction to a published quantile ladder.

fit_interval_calibrator.py measures, on the player-games a sportsbook actually
posted a line for, where outcomes really fall inside the range this platform
publishes. The answer is that the band is too wide at the bottom and the median
sits near the 44th percentile, so the map says which level to read the ladder at
to publish an honest one.

Corrections are per level, not per market. A level that did not improve on the
held-out season keeps its published value, so applying this can only touch the
levels that were shown to be wrong.
"""

import json
from pathlib import Path

import numpy as np

_CACHE: dict | None = None


def load(artifact_dir: Path) -> dict:
    global _CACHE
    if _CACHE is None:
        path = Path(artifact_dir) / "interval_calibrator.json"
        if path.exists():
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
            for m, e in sorted(_CACHE.items()):
                print(f"  interval calibrator: {m} corrects "
                      f"{', '.join(e.get('corrected_levels', []))}")
        else:
            _CACHE = {}
            print("  no interval calibrator found; ranges ship uncorrected")
    return _CACHE


def _read_ladder(read: float, have: list, ladder: list) -> float:
    """Value at a level, extrapolating past the ends of the ladder.

    np.interp clamps, and clamping defeats the whole correction at the top.
    The measured problem is that outcomes clear the published p90 between 11%
    and 18% of the time instead of 10%, so an honest p90 has to be read ABOVE
    level 0.90 -- and 0.90 is the highest level the ladder carries. Clamped,
    the p90 is returned unchanged and the only level that really needed moving
    is the one level that cannot move.

    So continue the ladder at the slope of its last segment. For a right skewed
    distribution that understates the tail, which is the safe direction: it can
    lift the ceiling toward where outcomes actually land without inventing a
    number the quantile models never implied. The same applies at the bottom,
    floored at zero because none of these markets go negative.
    """
    if len(have) < 2:
        return float(ladder[0]) if ladder else 0.0
    if read > have[-1]:
        span = have[-1] - have[-2]
        slope = (ladder[-1] - ladder[-2]) / span if span else 0.0
        return float(ladder[-1] + (read - have[-1]) * slope)
    if read < have[0]:
        span = have[1] - have[0]
        slope = (ladder[1] - ladder[0]) / span if span else 0.0
        return float(ladder[0] - (have[0] - read) * slope)
    return float(np.interp(read, have, ladder))


def apply(bundle: dict, market_code: str, qs: dict) -> dict:
    """Re-read a quantile ladder at the levels that make it honest.

    Takes and returns the same {level: value} shape. A market with no entry, or
    a ladder too short to interpolate, comes back untouched.
    """
    entry = (bundle or {}).get(market_code)
    if not entry or not qs:
        return qs
    levels = [float(q) for q in entry["levels"]]
    read_at = [float(r) for r in entry["read_at"]]

    have = sorted(float(q) for q in qs)
    ladder = sorted(float(qs[q]) for q in have)
    if len(have) < 2:
        return qs

    out = dict(qs)
    for level, read in zip(levels, read_at):
        if level not in qs:
            continue
        out[level] = float(max(0.0, _read_ladder(read, have, ladder)))

    # A corrected ladder still has to be a ladder. The levels are corrected
    # independently and the map is monotone, so crossings should not happen,
    # but publishing a p25 above a p50 would be worse than publishing an
    # uncorrected range and this costs nothing.
    keys = sorted(out)
    running = None
    for k in keys:
        if running is not None and out[k] < running:
            out[k] = running
        running = out[k]
    return out
