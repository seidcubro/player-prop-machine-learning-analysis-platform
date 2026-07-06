# Architecture

## Overview

PropSignal compares NFL sportsbook player-prop lines against internally generated
projections. The real, working pipeline (as opposed to earlier aspirational designs — see
"History" below) is:

1. **Ingestion** (`jobs/ingestion/`) — pull nflverse data into Postgres
2. **Feature engineering** (`services/api/app/routes/jobs.py`) — build rolling + contextual
   features per market into `player_market_features`
3. **Training** (`services/training/train.py`) — one RandomForest per market
4. **Edge calculation** (`services/training/build_prop_edges.py`) — match sportsbook props
   to model projections, compute edge/win probability, write `prop_edges`
5. **API / Frontend** — **not yet connected to step 4** (see Serving plane below)

## Components

### Data plane

- **`jobs/ingestion/`** — real, substantial ingestion pipeline
  (`app/etl/nflverse_ingest.py`, ~1600 lines) that populates `player_game_stats_app`,
  `nfl_games`, `snap_counts`, `ff_opportunity`, `injuries`, `depth_charts`, NGS tables, and
  more. This is **not** currently a `docker-compose` service (no service entry references
  its Dockerfile) — run it standalone:
  ```bash
  # build context must be the repo root (Dockerfile COPYs jobs/ingestion/app relative to it)
  docker build -f jobs/ingestion/Dockerfile -t propsignal-ingestion .
  docker run --rm --network player-prop-platform_default \
    -e POSTGRES_HOST=postgres propsignal-ingestion
  ```

### Feature engineering

- `POST /api/v1/jobs/build_features?market_code=X&lookback=N` (in
  `services/api/app/routes/jobs.py`) computes, per player/game, a lookback-window rolling
  feature set (mean/stddev/weighted_mean/trend), market-specific engineered features (target
  share, yards per target/carry/attempt, opponent defensive rates), rolling snap share, the
  nflverse `ff_opportunity` expected-usage model, and the **current game's** pre-game Vegas
  spread/total/weather from `nfl_games` — upserted into `player_market_features`.
- `POST /api/v1/jobs/attach_labels?market_code=X&lookback=N` fills in `label_actual` once the
  target game has been played.
- Position eligibility is enforced via `prop_markets.eligible_positions` (populated by
  `db/migrations/populate_eligible_positions.sql`), so training/eval never gets diluted with
  rows from positions that structurally never produce the stat (e.g. a defensive lineman's
  rushing yards).

### ML plane (`services/training/`)

Most development happens here.

- **`train.py`** — loads `player_market_features` for one `(market_code, lookback)`, joins
  `players` to filter by `eligible_positions`, flattens `extra_features` JSON into a feature
  matrix, does a time-ordered train/test split, trains a RandomForest (or GradientBoosting,
  by `MODEL_NAME` prefix `gb`), writes `{model_name}_{market_code}_lb{lookback}.joblib`/`.json`,
  updates `trained_models`/`active_models`.
- **`eval.py`** — independent, honest evaluation: rebuilds the exact same feature matrix as
  training (mirrors `train.py`'s `_build_feature_dataframe`), applies the model's
  `target_transform` if any, computes MAE/RMSE/R²/bias against a time-ordered held-out split,
  and compares against a naive `weighted_mean` baseline. This is the tool that caught the
  `log1p` regression and the position-filtering gap — see `docs/ML_PIPELINE.md`.
- **`build_prop_edges.py`** — loads sportsbook player props, matches each to the single
  most-recent `player_market_features` row as of the event date (never searches history for
  a "similar past matchup" — that was the root cause of the original overprojection bug),
  predicts, blends with `weighted_mean`, compares to the line, computes win probability and
  edge tier, writes `prop_edges`.

### Serving plane

- **`services/api/`** — FastAPI. Routes: player search/detail (`players.py`), feature-
  building jobs (`jobs.py`), Odds API sync (`odds.py`). See `docs/API.md` for the gap: no
  route currently serves `prop_edges`.
- **`services/inference/`** — intentionally still just a health-check placeholder
  (`app/main.py`), documented as a future extension point if inference needs to scale
  independently of `services/api`. This is unlike the deleted `jobs/etl`/`jobs/features`/
  `jobs/training` stubs (see History) — it's small, explicitly labeled, and still an active
  `docker-compose` service.

### UI

- **`apps/web/`** — React + Vite. Two pages today (`PlayersSearch.tsx`, `PlayerDetail.tsx`),
  built against `projection_ml`/`projection_baseline` only — no line/edge/odds concept yet.

## Data storage

- **Postgres** is the primary datastore.
- **Redis** is available for caching/job coordination; not yet used by any service.

## Artifacts

Training artifacts (`.joblib` + metadata `.json`) live in `services/training/artifacts/`,
bind-mounted read-only into the `api` container. **Not tracked in git** — regenerate by
retraining (see README "Local development").

## History — what got removed and why

Earlier iterations scaffolded a fuller intended architecture that was never built out:
`jobs/etl/`, `jobs/features/`, `jobs/training/` (empty stub entrypoints, literally
`return 0`), `libs/common_python/` (a shared package nothing ever imported),
`infra/terraform/` and `deploy/k8s/`/`deploy/helm/` (comment-only/empty deployment
scaffolding for a target that doesn't exist yet). These were deleted rather than kept as
"someday" placeholders, because they were actively misleading — the docs described them as
if they were part of the working pipeline. If/when real deployment infra or a separate
features/training job service is actually built, it should be added fresh against the real
current architecture, not resurrected from this scaffold.
