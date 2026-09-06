"""Pick the best model per market, with a selection rule that resists noise.

The trap in model selection is that the winner of a single train/test split is
often just the model that got the friendliest split. With ten candidates and one
split you are close to guaranteed to "find" an improvement that does not exist.

So every candidate is scored on the same expanding-window time-series folds:
train on everything before a cut, test on the block after it, walk the cut
forward. That mirrors how the model is actually used -- always predicting
forward, never with future data in the training set -- and it gives a
distribution of scores rather than a point estimate.

Selection rule: a challenger replaces the incumbent only if its mean R2 beats the
incumbent's by more than one standard error of the fold-to-fold difference. That
is a deliberately conservative bar. It means a challenger that is 0.003 better
on average but swings 0.02 between folds is correctly judged as noise.

Overfitting is reported explicitly as the train-minus-test R2 gap. A model with a
huge gap is memorising, and is flagged even if its test score looks fine.

Env: MARKET_CODE, LOOKBACK, N_FOLDS (default 5), plus the shared DB/artifact vars.
"""

import json
import os
from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.linear_model import ElasticNet, PoissonRegressor, Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import eval as ev

N_FOLDS = int(os.getenv("N_FOLDS", "5"))
SEED = 42

try:
    from lightgbm import LGBMRegressor

    HAS_LGBM = True
except Exception:  # ImportError, or OSError when libgomp is absent
    HAS_LGBM = False
    print("lightgbm unavailable; continuing without it")


def candidates(is_count_market: bool) -> dict:
    """Model zoo.

    Linear models are included as honesty checks, not because they are expected
    to win: if a ridge regression matches the forest, the extra capacity is not
    buying anything and the forest's complexity is unjustified.
    """
    zoo = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0, random_state=SEED)),
        "elasticnet": make_pipeline(
            StandardScaler(), ElasticNet(alpha=0.05, l1_ratio=0.3, random_state=SEED)
        ),
        "random_forest": RandomForestRegressor(
            n_estimators=400, max_depth=8, min_samples_leaf=5,
            max_features="sqrt", random_state=SEED, n_jobs=-1,
        ),
        "extra_trees": ExtraTreesRegressor(
            n_estimators=400, max_depth=12, min_samples_leaf=5,
            max_features="sqrt", random_state=SEED, n_jobs=-1,
        ),
        "hist_gbm": HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.05, max_depth=6,
            min_samples_leaf=20, l2_regularization=1.0, random_state=SEED,
        ),
        "gbm": GradientBoostingRegressor(
            n_estimators=300, learning_rate=0.05, max_depth=3,
            min_samples_leaf=20, subsample=0.9, random_state=SEED,
        ),
    }

    if HAS_LGBM:
        zoo["lightgbm"] = LGBMRegressor(
            n_estimators=600, learning_rate=0.03, num_leaves=31,
            min_child_samples=25, subsample=0.9, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0,
            random_state=SEED, n_jobs=-1, verbose=-1,
        )
        zoo["lightgbm_deep"] = LGBMRegressor(
            n_estimators=900, learning_rate=0.02, num_leaves=63,
            min_child_samples=40, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.7, reg_lambda=5.0,
            random_state=SEED, n_jobs=-1, verbose=-1,
        )

    if is_count_market:
        # Touchdowns and receptions are non-negative counts with variance that
        # grows with the mean, which is what Poisson deviance assumes and
        # squared error does not.
        zoo["hist_gbm_poisson"] = HistGradientBoostingRegressor(
            loss="poisson", max_iter=400, learning_rate=0.05, max_depth=6,
            min_samples_leaf=20, random_state=SEED,
        )
        zoo["poisson_glm"] = make_pipeline(
            StandardScaler(), PoissonRegressor(alpha=1.0, max_iter=500)
        )
        if HAS_LGBM:
            zoo["lightgbm_poisson"] = LGBMRegressor(
                objective="poisson", n_estimators=600, learning_rate=0.03,
                num_leaves=31, min_child_samples=25, reg_lambda=1.0,
                random_state=SEED, n_jobs=-1, verbose=-1,
            )

    return zoo


def expanding_folds(n: int, k: int):
    """Expanding-window splits over time-ordered rows.

    Each fold trains on everything before a cut and tests on the block after it,
    so no fold ever trains on data that comes after its own test set.
    """
    first = int(n * 0.45)
    step = (n - first) // (k + 1)
    for i in range(k):
        train_end = first + i * step
        test_end = train_end + step
        if test_end > n:
            break
        yield np.arange(0, train_end), np.arange(train_end, test_end)


def main():
    market = ev.MARKET_CODE
    is_count = market.endswith("_td") or market in {
        "recs", "rush_att", "pass_att", "pass_completions"
    }

    meta = ev.load_model_metadata()
    cols = meta["feature_cols"]
    df = ev.load_labeled_rows(cols).sort_values("as_of_game_date").reset_index(drop=True)
    X = ev.build_feature_matrix(df, cols)
    y = df[ev.LABEL_COL].astype(float).to_numpy()
    wm = df["weighted_mean"].astype(float).to_numpy()

    folds = list(expanding_folds(len(df), N_FOLDS))
    if not folds:
        raise SystemExit(f"{market}: too few rows ({len(df)}) to build folds")

    print(f"\n=== {market} | {len(df)} rows | {len(cols)} features | {len(folds)} folds ===")
    print(f"fold test sizes: {[len(te) for _, te in folds]}")

    # The naive rolling average, scored on exactly the same folds. Nothing that
    # fails to beat this is worth serving.
    base_r2 = [r2_score(y[te], np.clip(wm[te], 0, None)) for _, te in folds]
    print(f"\n{'model':<20}{'R2 mean':>9}{'R2 sd':>8}{'MAE':>8}{'overfit gap':>13}{'vs base':>9}")
    print("-" * 68)
    print(f"{'weighted_mean':<20}{np.mean(base_r2):>9.4f}{np.std(base_r2):>8.4f}"
          f"{np.mean([mean_absolute_error(y[te], wm[te]) for _, te in folds]):>8.3f}"
          f"{'-':>13}{'-':>9}")

    results = {}
    for name, model in candidates(is_count).items():
        fold_r2, fold_mae, gaps, diffs = [], [], [], []
        try:
            for i, (tr, te) in enumerate(folds):
                m = candidates(is_count)[name]
                m.fit(X.iloc[tr], y[tr])
                p_te = np.clip(m.predict(X.iloc[te]), 0, None)
                p_tr = np.clip(m.predict(X.iloc[tr]), 0, None)
                r2 = r2_score(y[te], p_te)
                fold_r2.append(r2)
                fold_mae.append(mean_absolute_error(y[te], p_te))
                gaps.append(r2_score(y[tr], p_tr) - r2)
                diffs.append(r2 - base_r2[i])
        except Exception as exc:
            print(f"{name:<20}failed: {type(exc).__name__}: {str(exc)[:40]}")
            continue

        results[name] = {
            "r2_mean": float(np.mean(fold_r2)),
            "r2_sd": float(np.std(fold_r2)),
            "mae_mean": float(np.mean(fold_mae)),
            "overfit_gap": float(np.mean(gaps)),
            "vs_baseline_mean": float(np.mean(diffs)),
            "vs_baseline_se": float(np.std(diffs) / max(1, np.sqrt(len(diffs)))),
            "fold_r2": [float(v) for v in fold_r2],
        }
        r = results[name]
        flag = "  <-- overfit" if r["overfit_gap"] > 0.35 else ""
        print(f"{name:<20}{r['r2_mean']:>9.4f}{r['r2_sd']:>8.4f}{r['mae_mean']:>8.3f}"
              f"{r['overfit_gap']:>13.3f}{r['vs_baseline_mean']:>+9.4f}{flag}")

    # Only models that clear the naive baseline by more than the fold-to-fold
    # noise in that comparison are eligible at all.
    eligible = {
        n: r for n, r in results.items()
        if r["vs_baseline_mean"] > r["vs_baseline_se"] and r["overfit_gap"] <= 0.35
    }
    if not eligible:
        print("\nNo candidate reliably beats the rolling average. Keeping the incumbent.")
        return

    best = max(eligible, key=lambda n: eligible[n]["r2_mean"])
    print(f"\nbest eligible: {best} (R2 {eligible[best]['r2_mean']:.4f} "
          f"+/- {eligible[best]['r2_sd']:.4f})")

    # Compare against the incumbent on a paired, per-fold basis: the standard
    # error of the *difference* is the right yardstick, not each model's own
    # spread, because both models saw identical folds.
    incumbent = "random_forest"
    if incumbent in results and best != incumbent:
        d = np.array(results[best]["fold_r2"]) - np.array(results[incumbent]["fold_r2"])
        se = d.std() / max(1, np.sqrt(len(d)))
        print(f"vs incumbent ({incumbent}): mean diff {d.mean():+.4f}, 1 SE {se:.4f}")
        if d.mean() > se:
            print(f"VERDICT: adopt {best} -- beats {incumbent} by more than one SE")
        else:
            print(f"VERDICT: keep {incumbent} -- {best}'s edge is within noise")
            best = incumbent
    else:
        print(f"VERDICT: adopt {best}")

    out = {
        "market_code": market,
        "generated_at": date.today().isoformat(),
        "rows": int(len(df)),
        "features": len(cols),
        "folds": len(folds),
        "baseline_r2_mean": float(np.mean(base_r2)),
        "winner": best,
        "results": results,
    }
    path = os.path.join(ev.ARTIFACT_DIR, f"bakeoff_{market}_lb{ev.LOOKBACK}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
