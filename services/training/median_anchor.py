"""Apply the fitted median anchor to a published median.

fit_median_anchor.py measures, per market and per projection band, the median of
actual/projection on graded walk-forward picks. Applying it re-reads the median
as that fraction of the point projection instead of taking it from the quantile
ladder, which lets the median inherit the point model's ability to extrapolate.

Only the median moves. The rest of the ladder is what it was, and the result is
clamped inside the band's own p25 and p75 so a corrected median can never leave
its own interval.
"""

import json
from pathlib import Path

_CACHE: dict | None = None


def load(artifact_dir: Path) -> dict:
    global _CACHE
    if _CACHE is None:
        path = Path(artifact_dir) / "median_anchor.json"
        if path.exists():
            _CACHE = json.loads(path.read_text(encoding="utf-8"))
            for m, e in sorted(_CACHE.items()):
                bands = ", ".join(sorted(e.get("bands", {})))
                print(f"  median anchor: {m} bands {bands}")
        else:
            _CACHE = {}
            print("  no median anchor found; medians come from the ladder alone")
    return _CACHE


def _band_of(projection: float, edges: list) -> int:
    """Which projection band this row falls in, matching pandas.cut order."""
    b = 0
    for e in edges:
        if projection > e:
            b += 1
    return b


def apply(bundle: dict, market_code: str, projection: float,
          median: float, p25=None, p75=None) -> float:
    """Return the median to publish. Unrecognised market or band is untouched."""
    entry = (bundle or {}).get(market_code)
    if not entry or projection is None or median is None:
        return median
    try:
        proj = float(projection)
    except (TypeError, ValueError):
        return median
    if proj <= 0:
        return median

    band = str(_band_of(proj, entry.get("edges", [])))
    cfg = entry.get("bands", {}).get(band)
    if not cfg:
        return median

    out = proj * float(cfg["ratio"])
    # Never outside its own interval.
    if p25 is not None:
        out = max(out, float(p25))
    if p75 is not None:
        out = min(out, float(p75))
    return float(max(0.0, out))
