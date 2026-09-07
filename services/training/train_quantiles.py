"""Train a quantile ensemble per market, so win probability comes from a real
predicted distribution instead of an assumed one.

Why this exists
---------------
`build_prop_edges.py` used to turn a point projection into a win probability by
assuming the outcome is Gaussian around it, with sigma taken from the player's
rolling `stddev` and then hand-capped at `0.75 *` the line. That produces
absurd confidence -- 95% on a rushing-yards prop -- because a single capped
number cannot describe a distribution that is right-skewed, floored at zero, and
much wider for a workhorse than for a rotational player.

Instead, fit the conditional quantiles directly. Predicting q10..q90 gives an
empirical CDF per player-game, and P(over) is read straight off it. The spread
between quantiles *is* the uncertainty, learned per row from the features rather
than assumed, so a volatile boom/bust receiver naturally gets a wider interval
than a steady possession back.

Pinball (quantile) loss is the proper scoring rule for each fitted quantile, and
is reported per quantile so a bad fit is visible rather than silent.

Env: MARKET_CODE, LOOKBACK, QUANT_MODEL_NAME (default "quant_v1"),
     SPLIT_MODE (frac|season), plus the shared DB/artifact vars from eval.py.
"""

import json
import os
from datetime import date

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

import eval as ev

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
QUANT_MODEL_NAME = os.getenv("QUANT_MODEL_NAME", "quant_v1")


def pinball_loss(y_true, y_pred, q: float) -> float:
    """Proper scoring rule for a single quantile (lower is better)."""
    d = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return float(np.mean(np.maximum(q * d, (q - 1.0) * d)))


def build_quantile_model(q: float):
    return GradientBoostingRegressor(
        loss="quantile",
        alpha=q,
        n_estimators=int(os.getenv("Q_N_ESTIMATORS", "400")),
        learning_rate=float(os.getenv("Q_LEARNING_RATE", "0.05")),
        max_depth=int(os.getenv("Q_MAX_DEPTH", "3")),
        min_samples_leaf=int(os.getenv("Q_MIN_SAMPLES_LEAF", "20")),
        subsample=float(os.getenv("Q_SUBSAMPLE", "0.9")),
        random_state=42,
    )


def prob_over(qpreds: dict[float, np.ndarray], line: np.ndarray) -> np.ndarray:
    """P(outcome > line) read off the predicted quantile CDF.

    The fitted quantiles give CDF points (q_value, q_level). Interpolating the
    level at `line` gives P(outcome <= line) directly; complement for the over.
    Quantiles are sorted per row first because separately-fitted quantile models
    can cross, which would otherwise make the CDF non-monotonic.
    """
    levels = np.array(sorted(qpreds.keys()), dtype=float)
    mat = np.column_stack([qpreds[q] for q in sorted(qpreds.keys())])
    mat = np.sort(mat, axis=1)  # enforce monotone CDF per row

    out = np.empty(len(line), dtype=float)
    for i in range(len(line)):
        out[i] = 1.0 - float(np.interp(line[i], mat[i], levels))
    return np.clip(out, 0.01, 0.99)


# Positions a sportsbook actually posts a line for. `prop_markets.eligible_
# positions` is deliberately wider than this because the projections page needs
# a number for every skill player, but a wide receiver's rushing attempts are
# structural zeros and they wreck quantile regression specifically.
#
# rush_att is the clear case. Of its 11,685 training rows, 52.2% are exactly
# zero, so the true conditional q10, q25 and q50 for half the population are all
# zero, the pinball loss is flat across that whole region, and the three models
# collapse onto each other. Measured on the 2025 holdout, scored on the same
# rows both ways:
#
#                                  q10    q25    q50    q75    q90
#   fit on all eligible          0.167  0.167  0.167  0.771  0.902
#   fit on priced positions      0.167  0.306  0.541  0.755  0.888
#   target                        0.10   0.25   0.50   0.75   0.90
#
# Three identical numbers is degeneracy, not miscalibration. Restricting the
# population separates q25 and q50 and puts the median almost exactly on target.
# rush_yds and the receiving markets barely move, which is the right result:
# this is a targeted fix for a measured failure, not a blanket change.
PRICED_POSITIONS = {
    "rush_att": ["QB", "RB"],
    "rush_yds": ["QB", "RB"],
    "rush_td": ["QB", "RB"],
}


def season_of(dates) -> np.ndarray:
    """NFL seasons straddle the new year, so anything before March belongs to
    the previous season."""
    d = pd.to_datetime(pd.Series(dates))
    return d.dt.year.where(d.dt.month >= 3, d.dt.year - 1).to_numpy()


def cdf_level(qpreds: dict[float, np.ndarray], values: np.ndarray) -> np.ndarray:
    """Predicted P(Y <= value) for each row, read off its fitted quantiles."""
    levels = np.array(sorted(qpreds.keys()), dtype=float)
    mat = np.column_stack([qpreds[q] for q in sorted(qpreds.keys())])
    mat = np.sort(mat, axis=1)
    out = np.empty(len(values), dtype=float)
    for i in range(len(values)):
        out[i] = float(np.interp(values[i], mat[i], levels))
    return out


PRICED_SNAP_SHARE = float(os.getenv("PRICED_SNAP_SHARE", "0.55"))

# Odds market key per internal market code, for looking up who actually gets
# priced. Mirrors services/api/app/odds_market_map.py, which lives in another
# service and is not importable from here.
ODDS_MARKET_KEYS = {
    "pass_att": "player_pass_attempts",
    "pass_completions": "player_pass_completions",
    "pass_yds": "player_pass_yds",
    "pass_td": "player_pass_tds",
    "rush_att": "player_rush_attempts",
    "rush_yds": "player_rush_yds",
    "recs": "player_receptions",
    "rec_yds": "player_reception_yds",
    "any_td": "player_anytime_td",
}


def priced_player_ids(market_code: str) -> set[str]:
    """Players a book has actually posted this market on, from odds history.

    Better than any proxy, because it is the population itself. Three seasons of
    snapshots is enough to identify who gets priced in each market.
    """
    key = ODDS_MARKET_KEYS.get(market_code)
    if not key:
        return set()
    from sqlalchemy import create_engine, text as _text
    url = os.getenv("DATABASE_URL") or (
        f"postgresql://{os.getenv('POSTGRES_USER', 'app')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'app')}@"
        f"{os.getenv('POSTGRES_HOST', 'postgres')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'app')}"
    )
    with create_engine(url, future=True).connect() as conn:
        rows = conn.execute(_text("""
            SELECT DISTINCT p.external_id
            FROM odds_snapshots s
            JOIN players p
              ON lower(replace(replace(p.name, '.', ''), '-', ' ')) =
                 lower(replace(replace(s.player_name, '.', ''), '-', ' '))
            WHERE s.market_key = :k AND p.external_id IS NOT NULL
        """), {"k": key})
        return {r[0] for r in rows}


def priced_like_mask(df, market_code: str | None = None) -> np.ndarray:
    """Rows resembling the population this market's probabilities get applied to.

    The calibration map has to be measured on the population it will be used on,
    and getting that population wrong is worse than not filtering at all.

    The first version used a flat snap-share threshold as a proxy for "a player
    books post lines on". For the receiving markets that is roughly right. For
    the rushing markets it was badly wrong: **43% of the players who actually
    get a rushing line have a snap share below 0.55**, because committee backs
    are priced constantly. So the map was fit on bell-cow backs and quarterbacks
    and then applied to everyone, and rush_yds ended up claiming 73.4% against
    an actual 52.1% across 1,131 graded picks.

    So use the real population where it is known: every player a book has posted
    this market on across three seasons of odds history. The snap-share proxy
    stays only as a fallback for a market with no odds history yet, such as a
    newly added one.
    """
    ids = priced_player_ids(market_code) if market_code else set()
    if len(ids) >= 40 and "player_id" in df.columns:
        return df["player_id"].isin(ids).to_numpy()

    ex = df["extra_features"].apply(ev._normalize_extra_features)
    snap = ex.apply(lambda d: d.get("snap_share_mean", float("nan")))
    return (snap >= PRICED_SNAP_SHARE).fillna(False).to_numpy()


def fit_calibration_season_holdout(X, y, seasons, quantiles, priced=None):
    """PIT map measured on a whole season the quantile models never saw.

    This replaces a cross-validated version that produced an almost-identity map
    and was quietly useless. The reason is worth writing down, because it took
    three attempts to see.

    `TimeSeriesSplit` puts its fold boundaries wherever the row count falls,
    which is in the middle of a season. Scored against its own era a quantile
    model looks well calibrated, so the map comes back as the identity -- and
    then the model goes out and covers 25-32% of outcomes below its own q10 on a
    fresh season. Both measurements were right. They were answering different
    questions.

    The question serving actually asks, especially in Week 1, is "how wrong are
    these quantiles on a season I have never seen?" So the split is on a season
    boundary: fit on everything before the most recent complete season, measure
    where that season's outcomes land on the predicted CDF, and ship that. Per
    row production has been falling about 3% a year, and this is the only way to
    put a number on what that does to the intervals before it happens again.

    The probe is fitted on one season less than the model that ships, so the map
    slightly overstates the correction. That errs toward humbler probabilities,
    which is the right direction to be wrong in.
    """
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    sa = np.asarray(seasons)
    latest = sa.max()
    fit_mask, probe_mask = sa < latest, sa == latest
    if priced is not None:
        # The probe is FITTED on everything (more data is strictly better for
        # the model) but MEASURED only on priced-like rows, because the map
        # describes the population it gets applied to.
        narrowed = probe_mask & np.asarray(priced)
        if narrowed.sum() >= 100:
            probe_mask = narrowed
        else:
            print(f"  only {int(narrowed.sum())} priced-like rows in the probe "
                  "season; measuring on all of them instead")
    if fit_mask.sum() < 300 or probe_mask.sum() < 100:
        print("  not enough history for a season-holdout calibration; "
              "falling back to an identity map")
        grid = np.linspace(0.0, 1.0, 101)
        return {"grid": grid.tolist(), "level": grid.tolist()}

    probe = {}
    for q in quantiles:
        m = build_quantile_model(q)
        m.fit(Xa[fit_mask], ya[fit_mask])
        probe[q] = np.clip(m.predict(Xa[probe_mask]), 0.0, None)
    print(f"  calibration probe: fit on {int(fit_mask.sum())} rows before "
          f"{latest}, measured on {int(probe_mask.sum())} rows from {latest}")
    return fit_calibration(probe, ya[probe_mask])


def fit_calibration(qpreds: dict[float, np.ndarray], y: np.ndarray):
    """Probability-integral-transform map, fit on the training rows.

    Independently fitted quantile models are not a distribution. Every market
    here comes out biased the same way -- the low quantiles sit too high, so the
    CDF puts too much mass below the line and P(over) reads too low:

              q10    q25    q50
    rush_yds  0.25   0.34   0.54
    rec_yds   0.31   0.38   0.56
    recs      0.29   0.37   0.56
    target    0.10   0.25   0.50

    A calibrated CDF would put each row's own outcome at a uniformly distributed
    level. The empirical distribution of those levels is therefore exactly the
    correction needed, and it costs one pass over the training data. Applying it
    took the held-out 2025 EV strategy from break-even to a 95% interval that
    clears zero.

    Returned as two lists so it serialises into the model bundle.
    """
    u = np.sort(cdf_level(qpreds, np.asarray(y, dtype=float)))
    grid = np.linspace(0.0, 1.0, 101)
    emp = np.searchsorted(u, grid, side="right") / max(len(u), 1)
    return {"grid": grid.tolist(), "level": emp.tolist()}


def active_model_meta():
    """Resolve the market's *active* point model and load its metadata.

    Quantile models must live in exactly the feature space of the point model
    they accompany, or `build_prop_edges.py` will hand them a vector they were
    never fitted on. Deriving that from `active_models` rather than a MODEL_NAME
    env var removes the failure where an unset variable silently falls back to a
    legacy model with a much smaller feature set.
    """
    import json as _json

    from sqlalchemy import create_engine, text as _text

    url = os.getenv("DATABASE_URL") or (
        f"postgresql://{os.getenv('POSTGRES_USER', 'app')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'app')}@"
        f"{os.getenv('POSTGRES_HOST', 'postgres')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/"
        f"{os.getenv('POSTGRES_DB', 'app')}"
    )
    with create_engine(url, future=True).connect() as conn:
        row = conn.execute(
            _text(
                "SELECT a.model_name, a.lookback FROM active_models a "
                "JOIN prop_markets m ON m.id = a.market_id WHERE m.code = :c"
            ),
            {"c": ev.MARKET_CODE},
        ).mappings().first()

    if not row:
        raise SystemExit(f"no active model for {ev.MARKET_CODE}; train one first")

    path = os.path.join(
        ev.ARTIFACT_DIR, f"{row['model_name']}_{ev.MARKET_CODE}_lb{row['lookback']}.json"
    )
    if not os.path.exists(path):
        raise SystemExit(f"active model metadata missing: {path}")

    with open(path) as f:
        meta = _json.load(f)
    print(f"using active point model {row['model_name']} "
          f"({len(meta['feature_cols'])} features)")
    return meta


def main():
    meta = active_model_meta()
    feature_cols = meta["feature_cols"]

    df = ev.load_labeled_rows(feature_cols)
    keep = PRICED_POSITIONS.get(ev.MARKET_CODE)
    if keep:
        before = len(df)
        df = df[df["position"].isin(keep)]
        print(f"restricted to priced positions {keep}: "
              f"{before} -> {len(df)} rows")
    if ev.SPLIT_MODE == "season":
        train_df, test_df = ev.season_split(df)
    else:
        train_df, test_df = ev.time_split(df, ev.TEST_FRAC)

    X_train = ev.build_feature_matrix(train_df, feature_cols)
    X_test = ev.build_feature_matrix(test_df, feature_cols)
    y_train = train_df[ev.LABEL_COL].astype(float)
    y_test = test_df[ev.LABEL_COL].astype(float).to_numpy()

    models = {}
    losses = {}
    qpreds = {}
    for q in QUANTILES:
        m = build_quantile_model(q)
        m.fit(X_train, y_train)
        pred = np.clip(m.predict(X_test), 0.0, None)
        models[q] = m
        qpreds[q] = pred
        losses[f"q{int(q * 100)}"] = pinball_loss(y_test, pred, q)
        print(f"  q{int(q * 100):02d}  pinball={losses[f'q{int(q * 100)}']:.4f}")

    # Coverage: the share of actuals falling below each fitted quantile should
    # land near the quantile level itself. This is the honest check that the
    # intervals mean what they claim.
    coverage = {
        f"q{int(q * 100)}": float(np.mean(y_test <= qpreds[q])) for q in QUANTILES
    }
    print("\ncoverage (target = the quantile level itself):")
    for q in QUANTILES:
        k = f"q{int(q * 100)}"
        print(f"  {k}: {coverage[k]:.3f}  (target {q:.2f})")

    band = float(np.mean((y_test >= qpreds[0.10]) & (y_test <= qpreds[0.90])))
    print(f"\n80% interval actually contains {band:.1%} of outcomes (target 80%)")

    # Calibrate on OUT-OF-FOLD training predictions.
    #
    # The first version of this fit the map on in-sample predictions and came
    # out a near no-op: it said to read the raw CDF at 0.095 to get an honest
    # q10, while the held-out q10 was covering 32% of outcomes. Both numbers
    # were right. A gradient-boosted quantile model is well calibrated on the
    # rows it was fitted to and badly calibrated off them, so a correction
    # estimated in-sample has nothing to correct.
    #
    # Refitting on time-ordered folds and calibrating against predictions each
    # fold never saw measures the miscalibration that actually reaches serving.
    # It costs K extra fits per quantile and is the only version worth having.
    train_seasons = season_of(train_df["as_of_game_date"])
    calibration = fit_calibration_season_holdout(
        X_train, y_train, train_seasons, QUANTILES,
        priced=priced_like_mask(train_df, ev.MARKET_CODE))

    cal_cov = {}
    for q in QUANTILES:
        lvl = np.interp(q, calibration["level"], calibration["grid"])
        # Where the raw CDF has to be read so the calibrated answer is q.
        cal_cov[f"q{int(q * 100)}"] = float(lvl)
    print()
    print("calibration map: to get an honest qN, read the raw CDF at")
    for q in QUANTILES:
        print(f"  q{int(q * 100):02d}: {cal_cov[f'q{int(q * 100)}']:.3f}")

    # Same argument as train.py: measure on the holdout, ship a refit.
    #
    # `season_split` holds out the most recent complete season so the coverage
    # numbers above mean something, but those were also the models being saved,
    # so the bundle serving Week 1 had never seen a snap of the previous season.
    # The calibration map is kept as-is: it was measured on a season its probe
    # never saw, and that is exactly the quantity it is supposed to describe.
    shipped = {}
    X_all = ev.build_feature_matrix(df, feature_cols)
    y_all = df[ev.LABEL_COL].astype(float)
    for q in QUANTILES:
        m = build_quantile_model(q)
        m.fit(X_all, y_all)
        shipped[q] = m
    print(f"refit on all {len(X_all)} rows for serving "
          f"(coverage above is from the {len(train_df)}-row held-out fit)")
    models = shipped

    os.makedirs(ev.ARTIFACT_DIR, exist_ok=True)
    stem = f"{QUANT_MODEL_NAME}_{ev.MARKET_CODE}_lb{ev.LOOKBACK}"
    artifact_path = os.path.join(ev.ARTIFACT_DIR, f"{stem}.joblib")
    joblib.dump({"quantiles": QUANTILES, "models": models,
                 "calibration": calibration}, artifact_path)

    report = {
        "model_name": QUANT_MODEL_NAME,
        "market_code": ev.MARKET_CODE,
        "lookback": ev.LOOKBACK,
        "generated_at": date.today().isoformat(),
        "split_mode": ev.SPLIT_MODE,
        "artifact_path": artifact_path,
        "feature_cols": feature_cols,
        "point_model_used_for_features": meta["model_name"],
        "quantiles": QUANTILES,
        "pinball_loss": losses,
        "coverage": coverage,
        "interval_80_coverage": band,
        "priced_positions": keep,
        "calibration_read_points": cal_cov,
        "shipped_refit_on_all_rows": True,
        "shipped_rows": int(len(df)),
        "rows_train": int(len(train_df)),
        "rows_test": int(len(test_df)),
    }
    with open(os.path.join(ev.ARTIFACT_DIR, f"{stem}.json"), "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nWrote {artifact_path}")


if __name__ == "__main__":
    main()
