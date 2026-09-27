"""Scoring rules, and confidence intervals that account for correlated props.

Two things this project has been doing without and paying for.

**Proper scoring rules.** Hit rate is not one: a forecaster who says 55% on
everything and one who says 90% and 20% alternately can post the same hit rate
while one of them is lying about what it knows. Brier and log loss are strictly
proper, meaning each is minimised in expectation only by the true probability
(Gneiting & Raftery 2007, JASA 102, 359-378). Brier additionally decomposes into
calibration plus refinement, which is the exact distinction between our model
ranking picks well and claiming too much about them.

The skill score matters more than the raw score. A Brier of 0.24 means nothing
until you know what the benchmark scores, and here the benchmark is not a naive
forecaster, it is the sportsbook. `brier_skill` against the de-vigged market
probability answers the only question that decides whether this project has a
reason to exist: does our number know anything the price does not?

**Clustered bootstrap.** Props in the same game are not independent. If a
quarterback throws for 400 yards then his top receiver went over, the game went
over, and the opposing running back probably went under. Treating 7,000 props as
7,000 independent observations understates every confidence interval, because
the effective sample size is closer to the number of games than the number of
props.

Mecha's xScore paper handles the same problem by resampling drives rather than
plays, since every play in a drive shares its outcome label. The equivalent here
is resampling games. The practical consequence is large: I spent a day acting on
1% differences between models, and a correctly clustered interval is wide enough
to say that most of them were noise. Anything reported without one of these
should be treated as a hypothesis.
"""

from __future__ import annotations

import numpy as np

EPS = 1e-12


def brier(y, p) -> float:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    return float(np.mean((p - y) ** 2))


def log_loss(y, p) -> float:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_skill(y, p, p_ref) -> float:
    """Fractional improvement in Brier over a reference forecaster.

    Positive means better than the reference, zero means indistinguishable from
    it, negative means worse. Reported as a fraction, so 0.02 is a 2% reduction
    in Brier score against the benchmark.
    """
    b, b_ref = brier(y, p), brier(y, p_ref)
    if b_ref <= EPS:
        return float("nan")
    return float((b_ref - b) / b_ref)


def ece(y, p, bins: int = 10) -> float:
    """Expected calibration error over quantile bins.

    Quantile bins rather than equal-width, so a bin is never nearly empty and
    the average is not dominated by a region the model rarely visits. Each bin
    contributes the absolute gap between what was predicted and what happened,
    weighted by how many rows fall in it.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(p) < bins * 2:
        bins = max(2, len(p) // 2)
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return float(abs(p.mean() - y.mean()))
    idx = np.clip(np.digitize(p, edges[1:-1], right=True), 0, len(edges) - 2)
    total, n = 0.0, len(p)
    for b in range(len(edges) - 1):
        m = idx == b
        if not m.any():
            continue
        total += m.sum() / n * abs(p[m].mean() - y[m].mean())
    return float(total)


def devig_two_way(price_a, price_b):
    """Fair probability of side A, with the bookmaker's margin removed.

    Both sides of a prop are priced to sum above 1; the excess is the hold and it
    is the bookmaker's fee, not a belief about the game. Comparing our
    probability to a vigged one flatters us by roughly half the hold, so the
    benchmark has to have it taken out or the comparison is rigged in our favour.

    Proportional (multiplicative) removal, which splits the margin in proportion
    to each side's implied probability. It is the standard choice and it is not
    the only defensible one: shin and additive methods distribute the margin
    differently, and on a heavy favourite they disagree by a percentage point or
    two. On props priced near even money the three agree closely, so the simplest
    is used.
    """
    a = implied_prob(price_a)
    b = implied_prob(price_b)
    total = a + b
    with np.errstate(invalid="ignore", divide="ignore"):
        fair = np.where(total > 0, a / total, np.nan)
    return fair


def implied_prob(price_american):
    p = np.asarray(price_american, dtype=float)
    return np.where(p > 0, 100.0 / (p + 100.0), -p / (-p + 100.0))


def clustered_bootstrap(stat_fn, groups, n_boot: int = 2000, seed: int = 0,
                        alpha: float = 0.05):
    """Percentile CI for `stat_fn(idx)`, resampling whole groups with replacement.

    `groups` is an array of group labels, one per row, typically a game key.
    `stat_fn` takes an array of row indices and returns a float. Resampling
    groups rather than rows preserves the within-game correlation structure, so
    the interval reflects the number of independent games rather than the number
    of props.

    Returns (point estimate, low, high). A statistic that cannot be computed on
    a particular resample (an empty market, say) should return NaN; those draws
    are dropped rather than propagating.
    """
    groups = np.asarray(groups)
    uniq, inverse = np.unique(groups, return_inverse=True)
    by_group = [np.flatnonzero(inverse == g) for g in range(len(uniq))]
    point = stat_fn(np.arange(len(groups)))
    if len(uniq) < 5:
        return point, float("nan"), float("nan")

    rng = np.random.default_rng(seed)
    draws = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([by_group[i] for i in pick])
        try:
            draws[b] = stat_fn(idx)
        except Exception:
            draws[b] = np.nan
    good = draws[np.isfinite(draws)]
    if len(good) < n_boot // 4:
        return point, float("nan"), float("nan")
    lo, hi = np.quantile(good, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)


def fmt_ci(point, lo, hi, pct: bool = False, places: int = 4) -> str:
    """One consistent rendering, so a number without an interval looks wrong."""
    if pct:
        if not np.isfinite(lo):
            return f"{point:+.1%} (CI n/a)"
        return f"{point:+.1%} [{lo:+.1%}, {hi:+.1%}]"
    if not np.isfinite(lo):
        return f"{point:.{places}f} (CI n/a)"
    return f"{point:.{places}f} [{lo:.{places}f}, {hi:.{places}f}]"


def crosses_zero(lo, hi) -> bool:
    """Whether an interval admits no effect, which is usually the real answer."""
    return not (np.isfinite(lo) and np.isfinite(hi)) or (lo <= 0.0 <= hi)
