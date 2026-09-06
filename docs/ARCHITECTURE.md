# Architecture

## The pipeline

Seven stages. Each one invalidates whatever the stages after it produced, which
is why ordering matters and why `scripts/refresh_pipeline.sh` exists.

**1. Ingestion** (`jobs/ingestion/`)

nflverse data into Postgres. Loads one season at a time and skips seasons that
aren't published yet, so a run in September doesn't die because the current year
has no stats file. Roughly 1,600 lines covering `player_game_stats_app`,
`nfl_games`, `snap_counts`, `ff_opportunity`, `injuries`, `depth_charts`,
`pbp_player_game`, NGS and PFR tables.

Not a compose service. Run it standalone, build context has to be the repo root:

```bash
docker build -f jobs/ingestion/Dockerfile -t propsignal-ingestion .
docker run --rm --network player-prop-platform_default \
  -e DATABASE_URL="postgresql://app:app@postgres:5432/app" \
  -e SEASON_START=2022 -e SEASON_END=2026 propsignal-ingestion
```

**2. Feature engineering** (`services/api/app/routes/jobs.py`)

Rolling and situational features per player per market into
`player_market_features`. Exposed as `POST /jobs/build_features` and
`/jobs/attach_labels`.

New seasons arrive with a NULL `team` column because that's populated by a
separate backfill, `db/backfills/fix_team_final.sql`. Run it after every
ingestion or the edge builder drops those rows.

**3. Training** (`services/training/train.py`)

One model per market. The family isn't fixed in advance, `bakeoff.py` picks it on
expanding-window folds. Three markets are served by linear models because that's
what won. Model name prefix selects the family: `ridge*`, `enet*`, `xtrees*`,
`pois*`, `gb*`, otherwise RandomForest.

Training marks the model active by default. Pass `ACTIVATE_MODEL=0` for
experiments, otherwise a throwaway sweep quietly takes over what the dashboard
serves.

**4. Quantiles** (`train_quantiles.py`)

q10 through q90 per market, so win probability comes off a predicted distribution
instead of an assumed one. These have to share the point model's exact feature
space or inference throws.

**5. Edge calculation** (`build_prop_edges.py`)

Match props to players, refresh everything known before kickoff, predict, compare
to the line, write `prop_edges`. Reads `active_models` to decide which model to
load. It used to have filenames hardcoded, which meant retraining had no effect
on anything anyone saw.

**6. Grading** (`grade_edges.py`)

Join past edges to what actually happened and store it in `prop_edge_results`.
That table is deliberately decoupled from `prop_edges` (no foreign key) because
edges get rebuilt on every run and the track record has to survive it.

**7. API and frontend**

`GET /api/v1/edges` serves upcoming edges. The dashboard and player pages consume
it.

One projection path, on purpose. An older `/projection_ml` endpoint built its own
projection with no freshness correction and disagreed with the edges the site
actually served. It's gone.

## Freshness

The thing that broke hardest. Anything known before kickoff (depth chart,
injuries, the line, weather, opponent) has to describe the game being predicted,
not whatever game the stored feature row happened to be about.

`build_prop_edges.py` calls `load_current_context()` and
`apply_current_context()` to substitute the upcoming game's values. Rolling
history features are left alone, those legitimately describe the past.

`audit_freshness.py` fails the build if any of it regresses. It works by seeding
every feature with a sentinel, running the refresh against a real upcoming game,
and checking what changed. Source inspection doesn't work here, features set
through a loop variable are invisible to it.

## Data storage

Postgres is the primary store. Redis is running but nothing uses it yet.

Model artifacts (`.joblib` plus metadata `.json`) live in
`services/training/artifacts/`, bind-mounted read-only into the api container.
Not in git, regenerate them with the refresh script.

## Analysis tools

These aren't part of the serving path. They're how I check whether any of it
works.

- `bakeoff.py` picks the model family per market on time-series folds
- `eval.py` honest held-out evaluation, supports `SPLIT_MODE=season`
- `backtest_season.py` trains on prior seasons, grades a full season against real
  captured lines
- `projection_log.py` compares projection, line and result per prop
- `eval_market_blend.py` fits how much weight to put on our number vs the line
- `eval_timeseries.py` compares level estimators (fixed window vs EWMA vs
  shrinkage)
- `eval_stale_role.py` measures and validates the demoted-player correction
- `diagnose_overproj.py` breaks error down by role, sample size and season phase
- `simulate.py` Monte Carlo game simulation
- `audit_freshness.py` the staleness gate

## History

Earlier versions of this repo had a lot of scaffolding that never ran: stub job
entrypoints, a separate inference service, aspirational specs describing things
that didn't exist. Most of it got removed. `services/inference/` is still a
placeholder, kept as an extension point if inference ever needs to scale away
from the API.
