
# ML Pipeline

## Goals

- Generate market-specific projections (baseline + ML)
- Train reproducible model artifacts that can be served by the API/inference service
- Keep training/inference configs explicit and versionable

## Stages

### 1) Ingestion
Populate database tables with player and game histories.

### 2) Feature engineering
Compute features by market and lookback window, e.g.:
- rolling mean / stddev
- weighted mean
- trend features

### 3) Training
For each `market_code` and `model_name`:
- load features and labels
- train model
- evaluate metrics
- write artifact bundle (model + metadata)

### 4) Serving
The API/inference service loads artifacts by:
- `market_code`
- `model_name`
- `lookback`

## Artifact contract (recommended)

Each trained artifact bundle should contain:

- `model.joblib` (serialized estimator)
- `metadata.json`:
  - `market_code`
  - `model_name`
  - `lookback`
  - `feature_cols`
  - `trained_at`
  - `metrics` (RMSE/MAE/etc.)
  - `data_window` / dataset version

This keeps serving logic simple and supports model versioning.
