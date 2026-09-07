"""Are the quarterback models too big for the data they have?

The recency test turned up a bias worth explaining. Across the volume markets the
models read 2-4% high, which is modest and roughly what the league-wide drift
implies. `pass_td` is the exception and it goes the other way: **-11.2%**, a
model predicting well under what actually happens.

Drift does not explain that one. The pass_td training seasons average 1.41, 1.25
and 1.26 touchdowns per row and the holdout lands at 1.30, so the eras match
almost exactly. A Poisson fit with a log link has zero mean residual on its own
training data by construction, so an 11% shortfall on a holdout with the same
mean is the model failing to generalise rather than the league moving.

The likely reason is capacity. Every quarterback market is QB-only, which leaves
**1,557 training rows**, and `pois_v3` runs `max_depth=6` for 300 boosting
iterations with `l2_regularization=0.0` on them. That is a lot of model for that
little data, and pass_td matters more than its size suggests: it was the single
best market in the EV backtest.

So sweep capacity against the holdout season, from the current settings down to
something a 1,500-row dataset can actually support, and include a plain Poisson
GLM as the floor -- if a linear model with no interactions holds its own, the
boosting was never earning its complexity.

Reported on MAE, on bias, and for the count markets on Poisson deviance, which
is the loss these are actually fit on and the honest scoring rule for a count. A
change only counts if it clears one standard error, the same bar the bakeoff
uses.

Env: MARKETS (default the four QB markets), SEASON, LOOKBACK.
"""

import json
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text

import eval as ev
import train as tr
from backtest_season import DATABASE_URL

MARKETS = os.getenv("MARKETS", "pass_td,pass_yds,pass_att,pass_completions").split(",")
SEASON = int(os.getenv("SEASON", "2025"))
COUNT_MARKETS = {"pass_td", "rush_td", "rec_td"}


def poisson_deviance(y, mu):
    """Mean Poisson deviance. The 0*log(0) term is defined as 0 by convention
    and numpy will not do that for you."""
    y = np.asarray(y, dtype=float)
    mu = np.clip(np.asarray(mu, dtype=float), 1e-9, None)
    term = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2.0 * np.mean(term - (y - mu)))


def candidates(is_count: bool):
    """Capacity ladder, from what ships today down to a linear floor."""
    def hgb(depth, iters, l2, leaf):
        return HistGradientBoostingRegressor(
            loss="poisson" if is_count else "squared_error",
            max_iter=iters, learning_rate=0.05, max_depth=depth,
            min_samples_leaf=leaf, l2_regularization=l2, random_state=42,
        )

    out = [
        ("current (depth 6, 300 iter, no l2)", hgb(6, 300, 0.0, 20)),
        ("depth 4, 200 iter, l2 1.0", hgb(4, 200, 1.0, 20)),
        ("depth 3, 150 iter, l2 1.0", hgb(3, 150, 1.0, 30)),
        ("depth 2, 100 iter, l2 5.0", hgb(2, 100, 5.0, 40)),
        ("depth 2, 60 iter, l2 10.0", hgb(2, 60, 10.0, 60)),
    ]
    # The floor. A log-link GLM for counts, a plain ridge otherwise. Both are
    # scaled because they are penalised and the features are on wildly
    # different units.
    if is_count:
        out.append(("Poisson GLM (linear floor)", Pipeline([
            ("scale", StandardScaler()),
            ("model", PoissonRegressor(alpha=1.0, max_iter=2000)),
        ])))
    else:
        out.append(("ridge (linear floor)", Pipeline([
            ("scale", StandardScaler()),
            ("model", Ridge(alpha=10.0)),
        ])))
    return out


def main():
    engine = create_engine(DATABASE_URL, future=True)
    summary = []

    for market in MARKETS:
        market = market.strip()
        os.environ["MARKET_CODE"] = market
        ev.MARKET_CODE = market
        with engine.connect() as c:
            row = c.execute(text(
                "SELECT a.model_name, a.lookback FROM active_models a "
                "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
            ), {"c": market}).mappings().first()
        if not row:
            print(f"{market}: no active model")
            continue
        meta_path = os.path.join(
            ev.ARTIFACT_DIR,
            f"{row['model_name']}_{market}_lb{row['lookback']}.json")
        if not os.path.exists(meta_path):
            print(f"{market}: no metadata")
            continue
        cols = json.load(open(meta_path))["feature_cols"]

        df = ev.load_labeled_rows(cols)
        d = pd.to_datetime(df["as_of_game_date"])
        yr = d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)
        train_df, test_df = df[yr < SEASON], df[yr == SEASON]
        if len(train_df) < 300 or len(test_df) < 80:
            print(f"{market}: not enough data")
            continue

        X = ev.build_feature_matrix(train_df, cols)
        Xt = ev.build_feature_matrix(test_df, cols)
        y = train_df[ev.LABEL_COL].astype(float).to_numpy()
        yt = test_df[ev.LABEL_COL].astype(float).to_numpy()
        is_count = market in COUNT_MARKETS

        print()
        print(f"=== {market} ({row['model_name']}) ===")
        print(f"{len(train_df)} train rows, {len(cols)} features, "
              f"{len(train_df) / max(len(cols), 1):.0f} rows per feature, "
              f"holdout {len(test_df)}")
        head = f"{'':<36}{'bias':>9}{'bias %':>9}{'MAE':>9}{'+/-':>7}"
        print(head + (f"{'deviance':>11}" if is_count else f"{'RMSE':>10}"))

        # The active model exactly as it ships, so the ladder is measured
        # against reality rather than against my reconstruction of it.
        live = tr.build_model(row["model_name"])
        live.fit(X, y)
        rows = []
        for label, model in [("ACTIVE: " + row["model_name"], live)] + \
                            candidates(is_count):
            if model is not live:
                model.fit(X, y)
            pred = np.clip(model.predict(Xt), 0, None)
            err = pred - yt
            mae = float(np.abs(err).mean())
            se = float(np.abs(err).std(ddof=1) / np.sqrt(len(yt)))
            extra = (poisson_deviance(yt, pred) if is_count
                     else float(np.sqrt((err ** 2).mean())))
            rows.append((label, float(err.mean()), mae, se, extra))
            print(f"{label:<36}{err.mean():>+9.3f}"
                  f"{err.mean() / max(yt.mean(), 1e-9):>+9.1%}"
                  f"{mae:>9.3f}{se:>7.3f}{extra:>11.4f}")

        # Judge a count market on its own loss, not on MAE.
        #
        # This script's first run picked on MAE for every market and reported
        # "no change" for pass_td, where MAE was identical to three decimals
        # while bias went from -11.2% to -1.5% and deviance fell 5%. MAE cannot
        # distinguish a symmetric error from a systematic shortfall, and for a
        # count it is not the loss the model is fitted on either. Index 4 holds
        # deviance for counts and RMSE otherwise.
        active = rows[0]
        metric = 4 if is_count else 2
        best = min(rows, key=lambda r: r[metric])
        gap = active[metric] - best[metric]
        scale = "deviance" if is_count else "MAE"
        if best is active:
            verdict = f"keep the active model (already best on {scale})"
        elif is_count:
            # There is no clean standard error for deviance here, so the bar is
            # a materially better loss AND a bias that is not worse. Reporting
            # both keeps the judgement visible rather than hidden in a rule.
            verdict = (f"switch to '{best[0]}': deviance {active[4]:.4f} -> "
                       f"{best[4]:.4f}, bias {active[1]:+.3f} -> {best[1]:+.3f}"
                       if gap > 0.01 and abs(best[1]) <= abs(active[1])
                       else f"no change: '{best[0]}' improves deviance by only "
                            f"{gap:.4f}")
        elif gap > active[3]:
            verdict = (f"switch to '{best[0]}': MAE {gap:.3f} better, "
                       f"clears the {active[3]:.3f} standard error; "
                       f"bias {active[1]:+.3f} -> {best[1]:+.3f}")
        else:
            verdict = (f"no change: best is '{best[0]}' at {gap:.3f} better on "
                       f"MAE, inside the {active[3]:.3f} standard error")
        print(f"  -> {verdict}")
        summary.append((market, verdict))

    print()
    print("=" * 74)
    for market, v in summary:
        print(f"{market:<20}{v}")


if __name__ == "__main__":
    main()
