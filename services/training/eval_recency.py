"""Does weighting recent seasons more heavily fix the drift?

Per-row production is falling about 3% a season -- rec_yds averaged 26.6 in 2022
and 23.4 in 2025, with no broken feature behind it. A model trained on all
seasons equally is therefore fitting a league that no longer exists, and it shows
up as bias: predictions read high all year, which is exactly why the held-out q10
covered 32% of outcomes instead of 10%.

Three ways to handle it, scored on the same holdout season:

  * **equal weight** -- what ships today.
  * **exponential decay** -- every season back gets multiplied by a factor, so
    2024 counts full, 2023 counts less, 2022 less again. Keeps all the data but
    lets it fade.
  * **recent seasons only** -- a hard cutoff. Cleaner, but throws away rows, and
    these markets are not so data-rich that this is free.

Judged on three things, in this order:

  1. **Bias** (mean prediction minus mean outcome). This is the thing drift
     actually breaks, and it is what makes the probabilities wrong. A model can
     have identical MAE and still be useless for pricing if it is biased.
  2. **MAE**, because a bias fix that costs real accuracy is not a fix.
  3. **Whether the gap clears one standard error.** The bakeoff already
     established that rule here and it exists to stop noise being read as an
     improvement.

Env: MARKETS, SEASON (holdout, default 2025), LOOKBACK.
"""

import json
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import eval as ev
import train as tr
from backtest_season import DATABASE_URL

MARKETS = os.getenv(
    "MARKETS",
    "rec_yds,rush_yds,pass_yds,recs,rush_att,pass_att,pass_completions,pass_td",
).split(",")
SEASON = int(os.getenv("SEASON", "2025"))
DECAYS = [float(x) for x in os.getenv("DECAYS", "0.85,0.70,0.50").split(",")]


def season_of(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1)


def fit_score(model_name, X, y, w, Xt, yt):
    """Fit with sample weights and report the metrics that matter here.

    Not every estimator in this project accepts sample_weight, so an unsupported
    one is reported rather than silently fitted unweighted, which would make the
    comparison a lie.
    """
    from sklearn.pipeline import Pipeline

    model = tr.build_model(model_name)
    kw = {}
    if w is not None:
        # A Pipeline refuses a bare sample_weight and wants it addressed to a
        # step, so route it to the final estimator. Scalers and imputers ahead
        # of it neither need nor accept weights.
        kw = ({f"{model.steps[-1][0]}__sample_weight": w}
              if isinstance(model, Pipeline) else {"sample_weight": w})
    try:
        model.fit(X, y, **kw)
    except (TypeError, ValueError) as exc:
        return {"unsupported": str(exc).split(".")[0]}
    pred = np.clip(model.predict(Xt), 0, None)
    err = pred - yt
    n = len(yt)
    return {
        "bias": float(err.mean()),
        "bias_pct": float(err.mean() / max(yt.mean(), 1e-9)),
        "mae": float(np.abs(err).mean()),
        # Standard error of the MAE, for the one-standard-error rule.
        "mae_se": float(np.abs(err).std(ddof=1) / np.sqrt(n)),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "n": n,
    }


def main():
    engine = create_engine(DATABASE_URL, future=True)
    verdicts = []

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
        yr = season_of(df["as_of_game_date"])
        train_df, test_df = df[yr < SEASON], df[yr == SEASON]
        if len(train_df) < 400 or len(test_df) < 100:
            print(f"{market}: not enough data")
            continue
        tr_yr = yr[yr < SEASON]
        newest = int(tr_yr.max())

        X = ev.build_feature_matrix(train_df, cols)
        Xt = ev.build_feature_matrix(test_df, cols)
        y = train_df[ev.LABEL_COL].astype(float).to_numpy()
        yt = test_df[ev.LABEL_COL].astype(float).to_numpy()
        age = (newest - tr_yr).to_numpy()

        print()
        print(f"=== {market} ({row['model_name']}) ===")
        seasons = sorted(tr_yr.unique())
        per_season = [f"{s}:{train_df[ev.LABEL_COL][tr_yr == s].mean():.2f}"
                      for s in seasons]
        print(f"train {len(train_df)} rows, mean outcome by season "
              f"{' '.join(per_season)}  ->  holdout {SEASON}: {yt.mean():.2f}")
        print(f"{'':<26}{'bias':>9}{'bias %':>9}{'MAE':>9}{'+/-':>7}{'RMSE':>9}")

        base = fit_score(row["model_name"], X, y, None, Xt, yt)
        if base is None or "unsupported" in base:
            print("  baseline fit failed, skipping market")
            continue
        print(f"{'equal weight (current)':<26}{base['bias']:>+9.3f}"
              f"{base['bias_pct']:>+9.1%}{base['mae']:>9.3f}"
              f"{base['mae_se']:>7.3f}{base['rmse']:>9.3f}")

        results = {"equal": base}
        for dcy in DECAYS:
            r = fit_score(row["model_name"], X, y, dcy ** age, Xt, yt)
            if r and "unsupported" in r:
                print(f"{'decay ' + str(dcy):<26}  not supported by this "
                      f"estimator: {r['unsupported']}")
            elif r:
                results[f"decay {dcy}"] = r
                print(f"{'decay ' + str(dcy):<26}{r['bias']:>+9.3f}"
                      f"{r['bias_pct']:>+9.1%}{r['mae']:>9.3f}"
                      f"{r['mae_se']:>7.3f}{r['rmse']:>9.3f}")

        for back in (1, 2):
            keep = age <= back
            if keep.sum() < 400:
                continue
            r = fit_score(row["model_name"], X[keep], y[keep], None, Xt, yt)
            if r and "unsupported" not in r:
                lab = f"last {back + 1} seasons only"
                results[lab] = r
                print(f"{lab:<26}{r['bias']:>+9.3f}{r['bias_pct']:>+9.1%}"
                      f"{r['mae']:>9.3f}{r['mae_se']:>7.3f}{r['rmse']:>9.3f}")

        # Pick on MAE, but only accept a change that clears one standard error.
        # Absolute bias is the tie-breaker, since that is the failure mode being
        # chased and two models with the same MAE are not equally useful when
        # one of them reads systematically high.
        best = min(results.items(), key=lambda kv: kv[1]["mae"])
        gap = base["mae"] - best[1]["mae"]
        if best[0] == "equal":
            verdict = "keep equal weighting (already best)"
        elif gap > base["mae_se"]:
            verdict = (f"switch to {best[0]}: MAE {gap:.3f} better, "
                       f"more than the {base['mae_se']:.3f} standard error")
        else:
            bias_gain = abs(base["bias"]) - abs(best[1]["bias"])
            verdict = (f"keep equal weighting: {best[0]} is only {gap:.3f} "
                       f"better on MAE, inside the {base['mae_se']:.3f} "
                       f"standard error (bias would improve by {bias_gain:+.3f})")
        print(f"  -> {verdict}")
        verdicts.append((market, verdict))

    print()
    print("=" * 70)
    for market, v in verdicts:
        print(f"{market:<20}{v}")


if __name__ == "__main__":
    main()
