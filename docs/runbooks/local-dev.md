# Runbook: local dev from scratch

This is the actual sequence used to stand up and exercise the full pipeline locally on
Windows. Requires Docker Desktop.

## 1. Start Docker Desktop and the base stack

```bash
# start Docker Desktop if not already running, then:
docker compose up -d postgres redis
```

`db/init.sql` runs automatically on first container creation (mounted into
`docker-entrypoint-initdb.d`). If the Postgres volume already exists, `init.sql` will not
re-run — apply new migrations/backfills manually (see `db/migrations/`, `db/backfills/`).

## 2. (If the DB is empty) run ingestion

```bash
docker build -f jobs/ingestion/Dockerfile -t propsignal-ingestion .
docker run --rm --network player-prop-platform_default \
  -e POSTGRES_HOST=postgres propsignal-ingestion
```

This populates `player_game_stats_app`, `nfl_games`, `snap_counts`, `ff_opportunity`,
`injuries`, `depth_charts`, NGS tables, etc. from nflverse.

## 3. Bring up the API

```bash
docker compose build api
docker compose up -d api
curl http://localhost:8000/health
```

## 4. Build features + attach labels for a market

```bash
curl -X POST "http://localhost:8000/api/v1/jobs/build_features?market_code=rec_yds&lookback=5"
curl -X POST "http://localhost:8000/api/v1/jobs/attach_labels?market_code=rec_yds&lookback=5"
```

Repeat per market (`recs`, `rec_td`, `rush_att`, `rush_yds`, `rush_td`, `pass_att`,
`pass_yds`, `pass_td`, `pass_completions`).

## 5. Train + evaluate

```bash
docker compose build training

docker compose run --rm -e MARKET_CODE=rec_yds -e MODEL_NAME=rf_posfilt_v4 -e LOOKBACK=5 \
  training python train.py

docker compose run --rm -e MARKET_CODE=rec_yds -e MODEL_NAME=rf_posfilt_v4 -e LOOKBACK=5 \
  training python eval.py
```

`eval.py`'s report (`services/training/artifacts/evals/*.json`) is the source of truth for
model quality — trust it over training's own printed metrics (see `docs/ML_PIPELINE.md`
History for why).

## 6. Build edges

Requires odds data in `odds_events`/`odds_player_props` (see `docs/API.md` "Odds sync", or
use whatever sample data is already loaded):

```bash
docker compose run --rm training python build_prop_edges.py
```

```bash
docker exec -it player-prop-platform-postgres-1 psql -U app -d app -c "SELECT * FROM prop_edges LIMIT 20;"
```

## 7. Frontend

```bash
cd apps/web
npm install
npm run dev
```

Expects the API at `http://localhost:8000` (`VITE_API_BASE` override available, see
`apps/web/src/api.ts`).

## Common gotchas

- **Windows path handling with `docker compose run`**: if a bind-mounted path gets mangled
  by Git Bash's automatic path conversion, prefix the command with `MSYS_NO_PATHCONV=1`.
- **`player_market_features.player_id` joins to `players.external_id`, never `players.id`**
  — this has been a recurring bug source (see `docs/ML_PIPELINE.md`/`docs/specs/data-model.md`).
- **`prop_markets` joins on `.code`, never a nonexistent `market_code` column.**
