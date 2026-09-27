"""Widen the published range to the width outcomes actually have.

The range on a player's page is too tight. Measured on walk-forward projections
against what happened (`research_interval_scale.py`), only 37 to 41% of outcomes
land inside the predicted middle 50%, where 50% is correct. A band drawn as "20
to 108" should read nearer "12 to 120", and a reader judging whether a projection
is confident is being shown about a third more certainty than the model has.

The factors are the multiple each market's ladder needs, measured as the ratio of
the median absolute error to the half-width the ladder claims:

    market      covered   needs
    recs          37.0%    1.41
    rush_att      37.7%    1.37
    rec_yds       38.9%    1.29
    rush_yds      41.3%    1.29

Only these four, because only these four had enough priced player-games with
outcomes to measure. The passing markets are almost certainly too tight as well
and are left alone rather than given a borrowed number, since a guessed
correction is worse than a known gap.

**This cannot move a pick, by construction rather than by care.** The tier is cut
on `gap_z`, which build_prop_edges computes from its own quantile dict; this
widens the ladder build_projections stores for the site. They are separate
paths reading separate objects. That separation is deliberate and worth keeping:
the thresholds in gap_tier were measured in the units of the uncorrected ladder,
so widening the ladder the tiers are cut on without dividing every threshold by
the same factor would silently halve the board.

The widening is around the median, not a scale of the whole ladder, because the
error is in the spread and not in the level. p50 does not move.

What this does not do is make the model more certain or less. It makes the
picture of its uncertainty honest, which is the only thing a range on a page is
for.
"""

from __future__ import annotations

import os

# Measured; see the module docstring and research_interval_scale.py. A market
# absent from this map is published unchanged.
FACTORS = {
    "recs": 1.41,
    "rush_att": 1.37,
    "rec_yds": 1.29,
    "rush_yds": 1.29,
}
ENABLED = os.getenv("WIDEN_INTERVALS", "1") != "0"

_stats = {"n": 0, "widened": 0.0}


def widen(qs: dict, market_code: str) -> dict:
    """Stretch a quantile ladder around its median. Returns a new dict.

    A ladder with no median, or a market with no measured factor, comes back
    untouched. Values stay non-negative, since no market here can go below zero,
    and the ladder stays ordered because a positive factor applied around a
    fixed point preserves order.
    """
    if not ENABLED or not qs:
        return qs
    factor = FACTORS.get(market_code)
    if not factor or factor == 1.0:
        return qs
    mid = qs.get(0.50)
    if mid is None:
        return qs
    try:
        mid = float(mid)
    except (TypeError, ValueError):
        return qs
    out = {}
    for q, v in qs.items():
        if v is None:
            out[q] = v
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            out[q] = v
            continue
        if q == 0.50:
            out[q] = val
            continue
        stretched = max(0.0, mid + (val - mid) * factor)
        _stats["n"] += 1
        _stats["widened"] += abs(stretched - val)
        out[q] = stretched
    return out


def applied() -> str:
    if not ENABLED:
        return "interval widening: off (WIDEN_INTERVALS=1 to publish honest bands)"
    if not _stats["n"]:
        return "interval widening: nothing to widen"
    return (f"interval widening: stretched {_stats['n']} quantile(s) to the "
            f"width outcomes actually have, by "
            f"{_stats['widened'] / _stats['n']:.2f} on average")
