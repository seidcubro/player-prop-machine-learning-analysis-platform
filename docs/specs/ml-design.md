# ML Design Spec

Last updated: 2026-07-05

This replaces an earlier version of this doc describing a Ridge-regression, per-market
`features_{market}` table design — that was never built. The real system is described
below and in `docs/ML_PIPELINE.md` (which also has the full bug-history — read that first
if you're touching this pipeline).

---

## 1. Architecture

Market-scoped ML, keyed by `(market_code, model_name, lookback)`. Current production model
family: `rf_posfilt_v4` (RandomForestRegressor, one per market, position-filtered).

## 2. Feature engineering

See `docs/ML_PIPELINE.md` for the full feature list. Summary: rolling
mean/stddev/weighted_mean/trend (lookback=5) + market-specific engineered ratios/rates +
rolling snap share + `ff_opportunity` expected usage + current-game Vegas context + injury
flags. All features are reproducible from `player_game_stats_app` + the supporting nflverse
tables via `POST /jobs/build_features`.

Position eligibility (`prop_markets.eligible_positions`) is enforced in both `train.py` and
`eval.py` — this was missing for most of the project's history (see ML_PIPELINE.md History)
and caused misleading cross-market R² comparisons.

## 3. Labels

`y` = realized value of `prop_markets.stat_field` for the target game (e.g. `rec_yds` ->
`receiving_yards`). Filled by `attach_labels` once the game has been played.

## 4. Training contract

Input: `player_market_features` (joined to `players` for position filtering) for one
`(market_code, lookback)`, `label_actual IS NOT NULL`.

Output: artifact bundle —
- `{model_name}_{market_code}_lb{lookback}.joblib` — serialized estimator
- `{model_name}_{market_code}_lb{lookback}.json` — metadata:
  - `market_code`, `market_id`, `lookback`, `model_type`, `target_transform`
  - `feature_cols` (exact column order the model expects — must be reproduced identically
    at inference time; `eval.py`'s `build_feature_matrix` and `build_prop_edges.py`'s
    per-row feature dict construction both do this)
  - `eligible_positions`, `train_rows`, `test_rows`, `train_date_min/max`, `test_date_min/max`
  - `mae`, `rmse`, `r2`, `feature_importances`

`target_transform` support: `"none"` (default) or `"log1p"` (train in `log1p(y)` space,
`expm1` at inference). **Currently unused in production** — see History in ML_PIPELINE.md
for why it was tried and reverted (it introduced a systematic underprediction bias).

## 5. Inference contract

Two separate paths exist today — this is a known architectural gap, not a design choice:

- **`build_prop_edges.py`** — full pipeline: load artifact, build feature vector from the
  most recent `player_market_features` row, predict, un-transform if needed, blend with
  `weighted_mean`, compare to a sportsbook line, compute edge/win probability. Writes
  `prop_edges`. Not exposed via any API route yet.
- **`services/api/app/routes/players.py` `projection_ml`** — loads the artifact from
  `active_models`, builds a feature vector from the single most recent
  `player_market_features` row, returns the raw prediction. No sportsbook comparison, no
  edge, no odds concept, and **no `target_transform` handling** (would silently return a
  log-space value if a `log1p` model were ever made active again — currently moot since no
  active model uses it, but worth fixing if that changes).

Failure modes handled: `unsupported_market` (market not found/inactive), `insufficient_data`
(no matching feature row), `artifact_missing`.

## 6. Evaluation

`eval.py` reports, per model: MAE, RMSE, R², bias (mean of prediction - actual, positive =
overprediction), broken down by position and by label-magnitude bucket, plus lift vs. a
naive `weighted_mean` baseline. Reports are written to
`services/training/artifacts/evals/{model_name}_{market_code}_lb{lookback}_eval.json`.

**Always trust `eval.py` over training's own printed metrics** — training's split can differ
subtly, and `eval.py` was specifically built to catch bias/leakage issues that a plain R²
number won't surface (see ML_PIPELINE.md History for how this caught a real, material bug).

## 7. Roadmap / untried ideas

See `docs/ML_PIPELINE.md` "Untried ideas for further R² improvement". The most concrete next
step for this pipeline specifically, though, is closing the two-inference-paths gap above:
either give `build_prop_edges.py`'s output a real API route, or fold its blend/edge logic
into `projection_ml` (and add `target_transform` handling there either way).
