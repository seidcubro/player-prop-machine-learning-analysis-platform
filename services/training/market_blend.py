"""Publish a projection that combines our estimate with the line.

The number on the board should be the best estimate available, and ours is not
it. Measured on graded picks (`research_market_regression.py`), the line is a
better point forecast than our projection in every market with enough rows:

    market     MAE ours   MAE line   fitted weight on ours
    recs         1.541      1.510            0.35
    rush_yds    19.908     18.817            0.25
    rec_yds     20.664     19.290            0.10
    rush_att     3.396      3.169            0.05

A weighted average of the two beats both on receptions and rush attempts and
sits level on the yardage markets. This is forecast combination, which has said
the same thing since Bates and Granger (1969): two forecasts whose errors are
not perfectly correlated combine into something better than either, and the
sportsbook's price is the aggregate of many models while ours is one.

Two properties make this safe to publish.

**It cannot change which side we pick.** A convex combination of the projection
and the line lies between them, so it is on the same side of the line as the
projection, always. Side selection, the probability and the tiers are untouched
by construction, not by care. What changes is only the number shown.

**It cannot produce an indefensible number.** At w = 0.25 a projection cannot
read 24 against a line of 38.5; the furthest it can fall is a quarter of the way
from the line toward whatever the model said. That is the entire complaint this
was built for, closed arithmetically rather than by hoping the model improves.

The weights are shrunk toward a pooled value rather than used as fitted.
Timmermann (2006) and Clemen (1989) both find that estimated combination weights
are frequently worse out of sample than equal or simple ones, because the weights
are themselves estimated from limited data and their error compounds. Ours come
from a few hundred rows per market. Shrinking halfway to the pooled mean keeps
the per-market ordering, which is large and believable, while refusing to trust
the third significant figure of any single market's fit.

Nothing here touches the model. If the projection improves, the fitted weight
should rise, and re-running the research script is how that gets noticed.
"""

from __future__ import annotations

import os

# Fitted on 2023-2024 and scored on 2025-2026 by research_market_regression.py.
# The weight is on OUR projection: 0.0 publishes the line, 1.0 publishes the
# model untouched.
FITTED = {
    "recs": 0.35,
    "rush_yds": 0.25,
    "rec_yds": 0.10,
    "rush_att": 0.05,
}
# Markets without enough graded rows to fit anything. They get the pooled value
# rather than a guess of their own or an exemption.
POOLED = round(sum(FITTED.values()) / len(FITTED), 2)
SHRINK = 0.5

# Off unless asked for. This changes every number on the board, so it ships
# behind a switch and gets turned on deliberately after a board has been looked
# at with it on.
ENABLED = os.getenv("MARKET_BLEND", "0") != "0"

_stats = {"n": 0, "moved": 0.0}


def weight(market_code: str) -> float:
    """How much of our own projection survives, per market."""
    fitted = FITTED.get(market_code)
    if fitted is None:
        return POOLED
    return round(SHRINK * fitted + (1.0 - SHRINK) * POOLED, 4)


def blend(projection: float, line: float, market_code: str) -> float:
    """Move `projection` toward `line`. Returns it unchanged when disabled.

    A missing or non-positive line means there is nothing to combine with, which
    is the normal case on the projections page for a player no book has priced.
    """
    if not ENABLED:
        return projection
    try:
        p = float(projection)
        ln = float(line)
    except (TypeError, ValueError):
        return projection
    if ln <= 0 or p < 0:
        return projection
    w = weight(market_code)
    out = w * p + (1.0 - w) * ln
    _stats["n"] += 1
    _stats["moved"] += abs(out - p)
    return max(0.0, out)


def applied() -> str:
    if not ENABLED:
        return "market blend: off (MARKET_BLEND=1 to publish combined numbers)"
    if not _stats["n"]:
        return "market blend: on, nothing had a line to combine with"
    return (f"market blend: combined {_stats['n']} projection(s) with the line, "
            f"moving each {_stats['moved'] / _stats['n']:.2f} on average")
