"""How much to trust the model against the line: the bias-variance dial.

Graded picks say two things at once. The line is close to unbiased: on the
priced player-games, the average outcome lands within a point or two of the
average line in nearly every bucket. And the model is overconfident everywhere:
it claims 60-65% on its picks and wins 50-57%. A model whose disagreements with
an accurate reference are partly noise has too much variance, not too much
bias, and the textbook correction is to shrink it toward the reference.

So the pick's probability becomes a blend of the two, in log-odds:

    logit(P) = logit(market) + w * (logit(model) - logit(market))

w is the whole dial. w = 1 is the model as it ships, all variance. w = 0 is
the line, all bias and no picks. Between them, the right w is whatever predicts
a season it was not fitted on best, which is what this measures. It is fitted
per market and side on every season before the holdout and scored on the
holdout, never on the rows it was fitted to.

Reported per group, on the holdout:

    w          the fitted weight on the model's disagreement
    claimed    what the model said, as published
    blended    what the blend says for the same picks
    won        what happened
    ll raw     log loss of the published probability
    ll blend   log loss of the blend. Lower is better; this decides.
    roi all    return on every published pick
    roi kept   return on the picks the blend still rates above break-even
               by at least KEEP_MARGIN, and how many that is

Read-only. Nothing is written and nothing on the board changes. The picks here
are the ones that were published, so this can re-rate a pick the model made but
cannot find one it missed; that needs the full priced population, which is the
next step if the blend earns it.
"""

import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'app')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'app')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'app')}"
)

MIN_FIT = 300
MIN_TEST = 80
# A pick the blend rates this far above break-even is kept. Two points, which
# is the smallest margin the tier cuts have ever treated as meaningful.
KEEP_MARGIN = 0.02
W_GRID = np.round(np.arange(0.0, 1.21, 0.02), 2)
EPS = 1e-6


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def implied(price):
    price = np.asarray(price, dtype=float)
    return np.where(price > 0, 100.0 / (price + 100.0), -price / (-price + 100.0))


def payout(price):
    """Profit on a winning one-unit stake."""
    price = np.asarray(price, dtype=float)
    return np.where(price > 0, price / 100.0, 100.0 / -price)


def log_loss(y, p):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def season_of(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def blend(model_p, market_p, w):
    lm = logit(market_p)
    return sigmoid(lm + w * (logit(model_p) - lm))


def fit_w(model_p, market_p, y) -> float:
    """The weight that minimises log loss on the fitting seasons.

    A grid rather than an optimiser: one parameter, a bounded range, and a
    result that is easy to read off. Allowed slightly above 1 so that a model
    which is under-confident rather than over-confident can say so.
    """
    losses = [log_loss(y, blend(model_p, market_p, w)) for w in W_GRID]
    return float(W_GRID[int(np.argmin(losses))])


def score(g: pd.DataFrame, w: float) -> dict:
    y = g["y"].to_numpy()
    mp = g["win_prob"].to_numpy()
    kp = g["implied"].to_numpy()
    bp = blend(mp, kp, w)
    pay = payout(g["price_american"].to_numpy())
    profit = np.where(y == 1, pay, -1.0)
    keep = bp - kp >= KEEP_MARGIN
    return {
        "n": len(g),
        "w": w,
        "claimed": float(mp.mean()),
        "blended": float(bp.mean()),
        "won": float(y.mean()),
        "ll_raw": log_loss(y, mp),
        "ll_blend": log_loss(y, bp),
        "roi_all": float(profit.mean()),
        "roi_kept": float(profit[keep].mean()) if keep.any() else float("nan"),
        "kept": int(keep.sum()),
    }


def main():
    eng = create_engine(DATABASE_URL, future=True)
    df = pd.read_sql(
        text(
            """
            SELECT market_code, recommended_side AS side, game_date,
                   win_prob, price_american, hit
            FROM prop_edge_results
            WHERE hit IS NOT NULL
              AND win_prob > 0 AND win_prob < 1
              AND price_american IS NOT NULL AND price_american <> 0
            """
        ),
        eng,
    )
    if df.empty:
        raise SystemExit("no graded picks to test against")

    df["y"] = df["hit"].astype(int)
    df["implied"] = implied(df["price_american"])
    df["season"] = season_of(df["game_date"])

    # Same holdout rule as the calibrators: the latest season with at least
    # half a typical season's rows, so a week of September is never the test.
    counts = df.groupby("season").size()
    eligible = counts[counts >= 0.5 * counts.median()]
    holdout = int(eligible.index.max())
    fit_all = df[df["season"] < holdout]
    test_all = df[df["season"] == holdout]
    print(f"fitting on seasons before {holdout} ({len(fit_all)} picks), "
          f"scoring on {holdout} ({len(test_all)} picks)\n")

    header = (f"{'group':<22}{'n':>6}{'w':>6}{'claimed':>9}{'blended':>9}"
              f"{'won':>7}{'ll raw':>8}{'ll bld':>8}{'roi all':>9}"
              f"{'roi kept':>10}{'kept':>6}")
    print(header)
    print("-" * len(header))

    def row(label, fit, test):
        if len(fit) < MIN_FIT or len(test) < MIN_TEST:
            print(f"{label:<22}{len(test):>6}  too few rows "
                  f"({len(fit)} to fit, {len(test)} to score)")
            return
        w = fit_w(fit["win_prob"].to_numpy(), fit["implied"].to_numpy(),
                  fit["y"].to_numpy())
        s = score(test, w)
        kept = "   -" if np.isnan(s["roi_kept"]) else f"{s['roi_kept']:+.1%}"
        better = "*" if s["ll_blend"] < s["ll_raw"] else " "
        print(f"{label:<22}{s['n']:>6}{s['w']:>6.2f}{s['claimed']:>9.1%}"
              f"{s['blended']:>9.1%}{s['won']:>7.1%}{s['ll_raw']:>8.4f}"
              f"{s['ll_blend']:>7.4f}{better}{s['roi_all']:>+9.1%}"
              f"{kept:>10}{s['kept']:>6}")

    # Pooled first: the most rows, so the steadiest estimate of the dial.
    row("ALL", fit_all, test_all)
    for side in ("under", "over"):
        row(f"all {side}s", fit_all[fit_all["side"] == side],
            test_all[test_all["side"] == side])
    print()
    for market in sorted(df["market_code"].unique()):
        for side in ("under", "over"):
            f = fit_all[(fit_all["market_code"] == market) & (fit_all["side"] == side)]
            t = test_all[(test_all["market_code"] == market) & (test_all["side"] == side)]
            row(f"{market} {side}", f, t)

    print(
        "\n* = the blend predicts the holdout better than the published "
        "probability.\n"
        "w near 1: trust the model as it is. w near 0: the line already knew.\n"
        "Anything in between is how far the model's disagreement should be\n"
        "believed. Nothing here changes the board."
    )


if __name__ == "__main__":
    main()
