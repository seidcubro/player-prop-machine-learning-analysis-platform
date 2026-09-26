"""Hold a projection near what the player has actually done this season.

A projection is defensible when you can say where it came from without
apologising for it. "He is averaging 76 rushing yards over two games and a good
matchup, so 70" is defensible. "He is averaging 76 and we project 43.8" is not,
whatever produced it, and the Week 3 2026 board was full of the second kind:

    TreVeyon Henderson   76 rush yds over two games,   projected 43.8
    Dontayvion Wicks     73.5 rec yds,                 projected 35.6
    Rashod Bateman       44 rec yds,                   projected 33.9
    Lamar Jackson        37 rush yds,                  projected 25.8

The cause was the feature window reaching into last December, and that is fixed
where it belongs, in the window. This is the guarantee rather than the fix. It
does not need the features rebuilt or a single model retrained, which is the
point: it corrects the board that is live now, and it keeps correcting any future
projection that wanders away from the player in front of it.

Standard shrinkage, with the weight set by how many games there are:

    published = w * model + (1 - w) * this season's average,  w = n / (n + K)

At two games that is half the model and half his own average. At five it stops
entirely, because by then the window is made of this season anyway and the model
is entitled to its own opinion. K = 2 rather than a round number because it puts
the halfway point at two games, which is the first point at which an average
exists at all.

What this deliberately does not do is claim to know better than the model. It
moves a projection toward an observed fact and away from an inference, in the one
situation where the inference is built on the least evidence. Once the window
change is rebuilt and the models retrained, the model's own output should already
sit near this anchor and the correction should be small: `applied()` reports the
average size of what it changed, so "small" is checkable rather than hoped for.

The honest caveat: this changes the point projection, and the median derived from
it is what picks sides. Correcting a median has cost this project money before
(MODEL.md, "Correcting the median to a true midpoint"). The exposure is bounded
by design, because the whole thing is inert from five games onward and so touches
Weeks 2 to 5 only, and it has not been backtested. It ships because a projection
that contradicts the player's own season is not defensible at any ROI.
"""

FULL_WINDOW = 5
K = 2.0

_stats = {"n": 0, "moved": 0.0}


def weight(n_games: float) -> float:
    """How much of the model's own opinion survives at n games."""
    return float(n_games) / (float(n_games) + K)


def anchor(pred: float, extra: dict, market_code: str = "") -> float:
    """Pull `pred` toward this season's average when there is little of it.

    `extra` is the row's extra_features. Returns `pred` untouched when the
    season average is missing or there are already enough games, so a caller can
    apply it unconditionally.
    """
    if not isinstance(extra, dict):
        return pred
    n = extra.get("y_season_n")
    # y_blend where the feature builder produced one: this season anchored to all
    # of last season's regular games, at the weight fitted per market. It beats
    # the raw season average as an estimator of the next game on every market
    # measured (research_season_boundary.py), by 5 to 7% on the volume markets,
    # and anchoring to the better of the two is free. y_season_mean is the
    # fallback for a row built before y_blend existed, and for a rookie, who has
    # no last season and whose y_blend is his season average anyway.
    mean = extra.get("y_blend")
    if mean is None:
        mean = extra.get("y_season_mean")
    if n is None or mean is None:
        return pred
    try:
        n = float(n)
        mean = float(mean)
        pred = float(pred)
    except (TypeError, ValueError):
        return pred
    # One game is enough to anchor to and zero is not. Five means the window is
    # this season already and there is nothing to correct.
    if n < 1 or n >= FULL_WINDOW or mean < 0:
        return pred
    w = weight(n)
    out = w * pred + (1.0 - w) * mean
    _stats["n"] += 1
    _stats["moved"] += abs(out - pred)
    return max(0.0, out)


def applied() -> str:
    """One line for the build log, so the size of this is never a guess."""
    if not _stats["n"]:
        return "thin-sample anchor: nothing to correct (every window is full)"
    return (f"thin-sample anchor: held {_stats['n']} projection(s) toward this "
            f"season's average, moving each by "
            f"{_stats['moved'] / _stats['n']:.2f} on average")
