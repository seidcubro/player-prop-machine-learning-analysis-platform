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

import math
import os

# In standard deviations of the player's own predicted distribution, and higher
# on the over side.
#
# The asymmetry is not a preference. Walk-forward over 2024-2025 on every priced
# prop rather than only the ones we published (`backtest_gap_system.py`), the two
# sides need different bars before they are worth anything:
#
#     |z| at least      unders                    overs
#       0.25            58.7%  +10.5% roi         51.3%   -2.3% roi
#       0.50            61.1%  +15.0%             52.9%   -0.4%
#       0.75            59.7%  +12.8%             55.3%   +2.4%
#       1.00            57.1%   +8.7%             57.6%   +5.7%
#       1.50            58.4%   +9.9%             68.4%  +23.3%
#
# Unders pay from a quarter of a standard deviation. Overs lose money until
# three quarters of one and only then start paying. Held to the same bar, overs
# became 70% of the published board and returned nothing, which dragged a +15%
# under side down to +4.3% overall.
#
# The direction is not discovered here. Books shade props toward the over because
# that is the side the public buys, so the over price is worst exactly where our
# own error is largest; the expected-value rule this replaced already held overs
# to double the under threshold for the same reason, from separate evidence. What
# this backtest supplies is the magnitude.
#
# The magnitudes are read off that table, which means they are fitted to it. They
# are kept at round numbers rather than the arg-max of any column, the 2024
# holdout is only 94 picks, and the over elite bar sits on 95 picks across two
# seasons. Treat the ladder as the finding and the exact cuts as provisional
# until a clean season confirms them.
ELITE = float(os.getenv("GAP_ELITE", "1.0"))
STRONG = float(os.getenv("GAP_STRONG", "0.75"))
MEDIUM = float(os.getenv("GAP_MEDIUM", "0.5"))
OVER_ELITE = float(os.getenv("GAP_OVER_ELITE", "1.5"))
OVER_STRONG = float(os.getenv("GAP_OVER_STRONG", "1.0"))
OVER_MEDIUM = float(os.getenv("GAP_OVER_MEDIUM", "0.75"))

# A floor per market, where the disagreement has to be larger before it means
# anything.
#
# Receiving yards was the one market on the board that lost money, at 52.1%
# against a 52.7% break-even, and it is the largest by pick count.
# research_rec_yds.py found the reason is not the model: every family calls the
# side at about 49.5%, a coin flip, and swapping rf_default for the ridge that
# beats it on MAE by 2.1% moves side accuracy by nothing. What separates this
# market is where the disagreement has to be before it pays:
#
#     |z|          rec_yds              recs
#     0.25-0.5     48.2%  -4.4% edge    53.2%  +0.8%
#     0.5-0.75     48.3%  -4.3%         57.1%  +4.1%
#     0.75-1.0     57.0%  +4.3%         55.5%  +0.6%
#     1.0+         55.3%  +2.5%         63.0%  +7.1%
#
# Receptions pay from a quarter of a standard deviation. Receiving yards lose
# steadily until three quarters of one and then pay. That is the market, not the
# fit: yards are catches times yards per catch, and the second term carries a
# tail nothing in a five-game window predicts. One broken tackle is forty yards.
# So the honest response is a higher bar here rather than a better model, and
# the loss autopsy agrees: "more work and bigger plays" is 34.0% of losses on
# this market against 1.0% of wins, the widest split anywhere.
MARKET_FLOOR_Z = {
    "rec_yds": float(os.getenv("GAP_FLOOR_REC_YDS", "0.75")),
}

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

# Off.
#
# This rule replaced expected-value tiering on 27 September, on the strength of
# research_gap_selection.py: ranked by expected value the hit rate ran 51.4,
# 51.9, 53.7, 53.6, 52.7 across quintiles, which is not a ranking, while ranked
# by the size of the disagreement it ran 48.7, 51.1, 53.8, 53.6, 57.3, which is.
# That was a true statement about ranking and it was never checked against the
# thing the product is actually judged on, which is units.
#
# Scored head to head on 8,548 graded rows across 2023-2025, both rules applied
# to identical rows with identical probabilities and quantiles:
#
#     selection                        picks     hit      ROI     units
#     EV elite+strong unders            2931   53.8%    +3.6%   +104.5
#     EV published board                4638   53.0%    +1.9%    +87.9
#     gap published board               1849   56.2%    +2.6%    +48.8
#     gap elite+strong unders           1002   55.5%    +2.2%    +22.3
#
# The gap rule is better per pick and publishes 60% fewer of them, and the
# selectivity costs far more than the quality gains. Nor is there a lower bar
# that fixes it: re-cutting the gap on unders alone gives -0.6% at 1.00, +1.9%
# at 0.75, +4.8% at 0.60, +2.5% at 0.50, +1.1% at 0.30. That is not a ladder,
# it is noise around a positive mean, and the best cut on it still returns
# +68.9 units against expected value's +104.6 on the same rows.
#
# So the board ranks on calibrated expected value again. Everything else that
# shipped alongside the gap rule is independent of it and stays: the season
# window, the depth chart fix, the starting-quarterback rule, the position
# eligibility, the priced-population filter.
#
# GAP_TIERS=1 turns it back on. The thresholds and the scale work in
# gap_tier.py and backtest_production.py stay here because the ranking result
# was real and may be worth something at a different bar; what it is not is a
# replacement for the rule it displaced.
ENABLED = os.getenv("GAP_TIERS", "0") != "0"


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


# Which spread divides the gap.
#
# "window": the standard deviation of the player's own recent games, which is
# the scale every threshold in this file was measured on, in
# backtest_gap_system.py and in research_gap_selection.py.
#
# "quantile": (q75 - q25) / 1.349 from the fitted ladder. This is what the
# board actually used until it was measured, and it is much too wide, because
# the ladder's lower quantiles are badly calibrated. Walk-forward over
# 2024-2026, share of outcomes landing below each predicted quantile:
#
#     market        <q10   <q25   <q50   <q75   <q90
#     target         10%    25%    50%    75%    90%
#     recs            4%     5%    47%    75%    89%
#     rush_att        0%    20%    46%    73%    87%
#     ALL             6%    15%    47%    74%    88%
#
# The median is fine. q25 is not, and q25 is half the width. So the quantile
# spread runs 1.4 to 1.9 times the window spread, every z comes out that much
# smaller, and the thresholds here silently became far stricter than the ones
# that were measured. It cost most of the board:
#
#     numerator / denominator       picks     hit              ROI    units
#     median     / window            1558   59.1% [56.3, 62.0]  +11.0%  +171.6
#     projection / window            1236   59.0% [56.1, 61.9]  +10.3%  +127.9
#     median     / quantile           696   57.9% [53.7, 62.2]   +8.0%   +55.5
#     projection / quantile           413   57.9% [52.5, 63.1]   +6.1%   +25.0
#
# Fixing the ladder's lower quantiles is the better long-term answer and is a
# separate job. Until then the gap is scaled by the spread it was measured on.
GAP_SCALE = os.getenv("GAP_SCALE", "window")


# The window spread and the ladder's spread are not the same size, so they
# cannot share a floor.
#
# MIN_SD above says what it is, in its own comment: "a predicted interquartile
# range below this is treated as no distribution at all". I reused it on the
# window standard deviation, which is 0.45 to 0.64 times as large, measured on
# the same priced props:
#
#     market      median window sd   median ladder sd   ratio
#     rec_yds              15.65             24.30      0.644
#     rush_yds             15.31             25.85      0.592
#     rush_att              2.40              4.34      0.553
#     recs                  1.17              2.61      0.447
#
# On mid-season rows that refused 10 to 20% of the board, which is bad and
# survived a backtest. In the first weeks of a season it is fatal: the
# season-only window holds two or three games then, and the standard deviation
# of three numbers is frequently zero. Going into Week 4 of 2026 the median
# window spread was 2.83 on receiving yards against a floor of 6.00, and 0.00
# on both rushing markets. The board came out with four picks on it, ninety
# minutes from kickoff, and that is entirely my doing.
WINDOW_PER_LADDER = {
    "rec_yds": 0.644, "rush_yds": 0.592, "rush_att": 0.553, "recs": 0.447,
}
# Markets with no priced history to measure the ratio on. Deliberately the
# conservative end of the measured range rather than the middle: a ratio that
# is too high floors too hard, and flooring too hard is the failure above.
WINDOW_PER_LADDER_DEFAULT = 0.45


def sd_for(q25, q75, window_sd, market_code: str) -> float | None:
    """The spread to divide by, in the units the thresholds were measured in.

    The window standard deviation when the window can support one, and the
    ladder's spread converted into window units when it cannot. One scale, one
    set of thresholds, and no row left without a scale just because the player
    has three quiet games behind him.

    The conversion is a measured constant per market, not a fudge: the two
    spreads describe the same uncertainty and differ by a stable factor. The
    alternative, which is what shipped for one day, is to refuse a tier
    whenever the window is thin, and a thin window is the normal state of
    affairs in September.
    """
    if GAP_SCALE != "window":
        return sd_from_quantiles(q25, q75, market_code)

    ratio = WINDOW_PER_LADDER.get(market_code, WINDOW_PER_LADDER_DEFAULT)
    floor = MIN_SD.get(market_code, MIN_SD_DEFAULT) * ratio

    try:
        sd = float(window_sd)
    except (TypeError, ValueError):
        sd = float("nan")
    if math.isfinite(sd) and sd >= floor:
        return sd

    # Thin or degenerate window: read the spread off the ladder instead and
    # convert. sd_from_quantiles applies the ladder's own floor, which is the
    # right one for the ladder's units.
    ladder = sd_from_quantiles(q25, q75, market_code)
    if ladder is None:
        return None
    converted = ladder * ratio
    return converted if converted >= floor else None


def gap_z(median_projection, line, q25, q75, market_code: str,
          window_sd=None) -> float | None:
    """Signed disagreement in standard deviations. Positive means over."""
    sd = sd_for(q25, q75, window_sd, market_code)
    if sd is None:
        return None
    try:
        z = (float(median_projection) - float(line)) / sd
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return max(-Z_CEILING, min(Z_CEILING, z))


def tier_for(z: float | None, market_code: str = "") -> str:
    """The published tier, from the size of the disagreement and the side.

    The sign of z is the side: positive is an over. Overs are held to the higher
    ladder, and a market in MARKET_FLOOR_Z is held to a higher one again, both
    for reasons set out above.
    """
    if z is None:
        # No usable distribution is not a small edge, it is no measurement. The
        # pick is shown as context rather than silently ranked as weak.
        return "small"
    over = z > 0
    elite = OVER_ELITE if over else ELITE
    strong = OVER_STRONG if over else STRONG
    medium = OVER_MEDIUM if over else MEDIUM
    floor = MARKET_FLOOR_Z.get(market_code)
    if floor is not None:
        # Raises whichever bars sit below the market's own floor, so a market
        # that only pays from 0.75 cannot reach a published tier below it while
        # the ordering above stays as it was.
        medium = max(medium, floor)
        strong = max(strong, floor)
        elite = max(elite, floor)
    a = abs(z)
    if a >= elite:
        return "elite"
    if a >= strong:
        return "strong"
    if a >= medium:
        return "medium"
    if a >= medium / 2.0:
        return "small"
    return "none"


def describe() -> str:
    return (f"gap tiers: unders elite >= {ELITE:g} sd, strong >= {STRONG:g}, "
            f"medium >= {MEDIUM:g}; overs elite >= {OVER_ELITE:g}, "
            f"strong >= {OVER_STRONG:g}, medium >= {OVER_MEDIUM:g}")
