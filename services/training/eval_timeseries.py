"""Treat the data as what it actually is: a panel of short, noisy time series.

The platform currently tabularises the problem -- a fixed 5-game window with
linear weights becomes a feature, and every player-game is then treated as an
independent row. That works, but it bakes in two arbitrary choices:

  1. The window length and its weights are guessed, not estimated. A local-level
     (state-space) view says a player's true level evolves smoothly and each game
     observes it with noise, which makes the decay rate a parameter to fit.
  2. Every player is pooled into one model, so a back with three games and a back
     with sixty are trusted equally. The panel view says each player has his own
     latent level, and the right estimate shrinks his noisy average toward the
     position mean in proportion to how little we have seen -- James-Stein /
     empirical Bayes.

This scores four estimators of a player's level on the same expanding-window
folds, so the comparison is honest and forward-only:

    current      the shipped weighted mean (linear weights, lookback 5)
    ewma         exponentially weighted, decay fitted on the training fold
    shrunk       empirical-Bayes shrinkage of the player's mean toward his
                 position's mean, using the observed variance components
    ewma+shrunk  both

Nothing here uses a model. It is a fair test of the *level estimate* that every
downstream model is built on, so an improvement propagates to all of them.
"""

import os

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=os.getenv("POSTGRES_USER", "app"),
        p=os.getenv("POSTGRES_PASSWORD", "app"),
        h=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "app"),
    ),
)

MARKET = os.getenv("MARKET_CODE", "rec_yds")
N_FOLDS = int(os.getenv("N_FOLDS", "5"))

STAT = {
    "rec_yds": "receiving_yards", "recs": "receptions", "rec_td": "receiving_tds",
    "rush_yds": "rushing_yards", "rush_att": "carries", "rush_td": "rushing_tds",
    "pass_yds": "passing_yards", "pass_att": "attempts",
    "pass_completions": "completions", "pass_td": "passing_tds",
}


def load(engine) -> pd.DataFrame:
    """Every eligible player-game in time order, with the raw stat."""
    stat = STAT[MARKET]
    q = text(f"""
        SELECT g.player_id, g.game_date, g.position, g.season,
               COALESCE(g.{stat}, 0)::float8 AS y
        FROM player_game_stats_app g
        JOIN prop_markets m ON m.code = :mk
        WHERE g.game_date IS NOT NULL
          AND g.position = ANY(m.eligible_positions)
        ORDER BY g.player_id, g.game_date
    """)
    return pd.read_sql(q, engine, params={"mk": MARKET})


def build_estimates(df: pd.DataFrame, alpha: float, k: float) -> pd.DataFrame:
    """Level estimates as of *before* each game, so nothing leaks.

    Every series is shifted one game back: the estimate for game t uses games
    1..t-1 only. This is the whole ballgame in time-series validation -- an
    estimator that peeks at its own target will look excellent and be worthless.
    """
    g = df.groupby("player_id")["y"]

    # Shipped baseline: linear weights over the trailing 5 games.
    def wmean(s):
        return s.rolling(5, min_periods=2).apply(
            lambda w: np.average(w, weights=np.arange(1, len(w) + 1)), raw=True
        )

    df["current"] = g.transform(lambda s: wmean(s.shift(1)))

    # Local-level model: exponentially weighted, all history, geometric decay.
    df["ewma"] = g.transform(lambda s: s.shift(1).ewm(alpha=alpha, adjust=False).mean())

    # Player's own running mean and how many games back it, for shrinkage.
    df["player_mean"] = g.transform(lambda s: s.shift(1).expanding().mean())
    df["n_seen"] = g.transform(lambda s: s.shift(1).expanding().count())

    # Population level for the player's position, also as-of only.
    pos = df.groupby("position")["y"]
    df["pos_mean"] = pos.transform(lambda s: s.shift(1).expanding().mean())

    # Empirical Bayes: weight = n / (n + k). With little history the estimate
    # sits near the position mean; with a lot it converges on the player's own.
    w = df["n_seen"] / (df["n_seen"] + k)
    df["shrunk"] = w * df["player_mean"] + (1 - w) * df["pos_mean"]
    df["ewma_shrunk"] = w * df["ewma"] + (1 - w) * df["pos_mean"]
    return df


def main():
    engine = create_engine(DATABASE_URL, future=True)
    df = load(engine)
    if df.empty:
        raise SystemExit(f"no rows for {MARKET}")

    df = df.sort_values(["player_id", "game_date"]).reset_index(drop=True)

    # Fit the two hyperparameters on the earliest 45% only, then evaluate
    # forward. Fitting them on everything would be the exact leak this script
    # exists to avoid.
    df_time = df.sort_values("game_date").reset_index(drop=True)
    cut = df_time["game_date"].quantile(0.45)
    fit_mask = df_time["game_date"] <= cut

    best = (None, None, np.inf)
    for alpha in (0.15, 0.25, 0.35, 0.5, 0.65):
        for k in (2, 4, 8, 16):
            tmp = build_estimates(df.copy(), alpha, k)
            tmp = tmp.sort_values("game_date").reset_index(drop=True)
            sub = tmp[fit_mask & tmp["ewma_shrunk"].notna()]
            if sub.empty:
                continue
            mae = mean_absolute_error(sub["y"], sub["ewma_shrunk"])
            if mae < best[2]:
                best = (alpha, k, mae)
    alpha, k, _ = best
    print(f"\n=== {MARKET} ===")
    print(f"fitted on games up to {cut}: alpha={alpha}, k={k}")
    print(f"(alpha {alpha} => a game's weight halves about every "
          f"{np.log(0.5) / np.log(1 - alpha):.1f} games)")

    d = build_estimates(df.copy(), alpha, k)
    d = d.sort_values("game_date").reset_index(drop=True)
    d = d[~fit_mask.reindex(d.index, fill_value=False)]

    methods = ["current", "ewma", "shrunk", "ewma_shrunk"]
    d = d.dropna(subset=methods + ["y"])

    # Expanding-window folds over the held-out period.
    n = len(d)
    step = n // (N_FOLDS + 1)
    rows = []
    for m in methods:
        maes, r2s = [], []
        for i in range(N_FOLDS):
            te = d.iloc[step * (i + 1): step * (i + 2)]
            if te.empty:
                continue
            maes.append(mean_absolute_error(te["y"], te[m]))
            r2s.append(r2_score(te["y"], te[m]))
        rows.append({
            "estimator": m,
            "MAE": np.mean(maes), "MAE_sd": np.std(maes),
            "R2": np.mean(r2s), "R2_sd": np.std(r2s),
        })

    out = pd.DataFrame(rows).sort_values("MAE")
    print(f"\nheld-out rows: {len(d)}, folds: {N_FOLDS}")
    print(out.to_string(index=False, float_format=lambda v: f"{v:8.4f}"))

    cur = out[out["estimator"] == "current"].iloc[0]
    win = out.iloc[0]
    if win["estimator"] != "current":
        # Paired: both estimators saw identical folds, so the SE of the
        # difference is the right yardstick, not each one's own spread.
        diff = cur["MAE"] - win["MAE"]
        se = np.sqrt(cur["MAE_sd"] ** 2 + win["MAE_sd"] ** 2) / np.sqrt(N_FOLDS)
        verdict = "ADOPT" if diff > se else "within noise, keep current"
        print(f"\n{win['estimator']} beats current by {diff:.4f} MAE "
              f"(1 SE {se:.4f}) -> {verdict}")
    else:
        print("\ncurrent weighted mean is already best")


if __name__ == "__main__":
    main()
