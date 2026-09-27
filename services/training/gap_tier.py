"""Rank a pick by how far the projection is from the line, not by the price.

The board used to tier on expected value, which is the disagreement converted to
a probability and then weighed against the odds. That rule calls a coin flip
elite when the coin flip pays +150, and it did: Baker Mayfield projected 1.5
passing touchdowns into a 1.5 line, priced at +150 on the under, arrived as an
elite pick. It is a correct expected-value calculation and a stupid thing to
publish.

Measured on 7,153 graded picks (`research_gap_selection.py`), the disagreement
is the better ranking and the price is the worse one:

    ranked by the gap, in standard deviations     ranked by expected value
      0.10 sd    48.7%                              lowest EV   51.4%
      0.19 sd    51.1%                                          51.9%
      0.28 sd    53.8%                                          53.7%
      0.41 sd    53.6%                                          53.6%
      0.72 sd    57.3%                             highest EV   52.7%

The gap is monotonic and expected value is not: its top bucket wins less often
than its middle, because the highest expected values are found at the longest
prices rather than on the most likely outcomes. And 27% of the picks that reached
a bet tier under the old rule sat within a quarter of a standard deviation of the
line, hitting 49.7%. A quarter of the board was noise with good odds attached.

At a full standard deviation or more the same picks hit 60.4% [56.2%, 65.5%]
against a 54.2% break-even, which is where the 60% target actually lives.

So: tier on |z|, where z is (median projection - line) divided by the width of
the predicted distribution. Standard deviations rather than yards, because two
yards of disagreement on a 15-yard line is a different claim from two yards on an
80-yard line, and because the width is exactly the model's own statement of how
sure it is.

The price is still shown and still used to compute expected value, which is still
published. It no longer decides what gets recommended. A pick this model is
confident about at -190 is a pick this model is confident about, and whether to
take it at that price is a question for whoever is betting: `negative_ev` marks
the ones the price does not cover so that information is on the board rather than
enforced by it.

Two guards the threshold needs.

A degenerate distribution produces a meaningless z. When the predicted quartiles
collapse, the denominator goes to zero and the gap explodes: the current board
holds one with z = 25.8. Those are a modelling failure, not a strong opinion, so
a floor on the width and a ceiling on z both apply.

And the season-by-season record is not uniform: +6.8% in 2024 and +3.9% in 2025,
but -0.6% in 2023. The rule is not fitted, so it cannot be overfitted, but the
edge is concentrated in the recent seasons and the thresholds are set where the
buckets were measured rather than tuned to maximise anything.
"""

from __future__ import annotations

import os

# In standard deviations of the player's own predicted distribution.
#
# Set at the bucket boundaries the research script measured, not tuned. Anything
# under MEDIUM is published as context: shown on the board, labelled, and not
# recommended.
ELITE = float(os.getenv("GAP_ELITE", "1.0"))
STRONG = float(os.getenv("GAP_STRONG", "0.75"))
MEDIUM = float(os.getenv("GAP_MEDIUM", "0.5"))

# A predicted interquartile range below this is treated as no distribution at
# all. In the market's own units, so a tenth of a reception and three yards.
MIN_SD = {
    "recs": 0.35, "rec_yds": 6.0, "rush_yds": 5.0, "rush_att": 0.6,
    "pass_yds": 15.0, "pass_att": 2.0, "pass_completions": 1.5,
    "pass_td": 0.35, "rush_td": 0.25, "rec_td": 0.25, "any_td": 0.25,
}
MIN_SD_DEFAULT = 0.25
# Beyond this the number is not a claim about football.
Z_CEILING = 6.0

ENABLED = os.getenv("GAP_TIERS", "1") != "0"


def sd_from_quantiles(q25, q75, market_code: str) -> float | None:
    """Width of the predicted distribution, as a standard deviation.

    (q75 - q25) / 1.349 is the normal-consistent estimator, which is what makes
    z comparable across markets whose distributions have different shapes. It is
    an approximation on a count market and a good enough one: the alternative is
    a per-market variance model that would need its own validation.
    """
    try:
        lo, hi = float(q25), float(q75)
    except (TypeError, ValueError):
        return None
    if not (hi > lo):
        return None
    sd = (hi - lo) / 1.349
    floor = MIN_SD.get(market_code, MIN_SD_DEFAULT)
    return sd if sd >= floor else None


def gap_z(median_projection, line, q25, q75, market_code: str) -> float | None:
    """Signed disagreement in standard deviations. Positive means over."""
    sd = sd_from_quantiles(q25, q75, market_code)
    if sd is None:
        return None
    try:
        z = (float(median_projection) - float(line)) / sd
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return max(-Z_CEILING, min(Z_CEILING, z))


def tier_for(z: float | None) -> str:
    """The published tier, from the size of the disagreement alone."""
    if z is None:
        # No usable distribution is not a small edge, it is no measurement. The
        # pick is shown as context rather than silently ranked as weak.
        return "small"
    a = abs(z)
    if a >= ELITE:
        return "elite"
    if a >= STRONG:
        return "strong"
    if a >= MEDIUM:
        return "medium"
    if a >= MEDIUM / 2.0:
        return "small"
    return "none"


def describe() -> str:
    return (f"gap tiers: elite >= {ELITE:g} sd, strong >= {STRONG:g}, "
            f"medium >= {MEDIUM:g}, below {MEDIUM / 2:g} not published")
