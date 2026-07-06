# PropSignal — Player Prop Machine Learning Platform

A personal, full-stack machine learning platform for comparing NFL sportsbook player-prop
lines against internally generated projections.

Pipeline:

```
Historical NFL data (nflverse)
  -> Rolling + contextual feature generation (player_market_features)
  -> Market-specific ML models (one RandomForest per market)
  -> Sportsbook odds (The Odds API)
  -> Edge calculation (projection vs. line -> win probability -> edge tier)
  -> API
  -> Frontend dashboard
```

---

## Current status (honest, as of this writing)

- **Data + features + training + edge calculation work correctly and are validated.**
  `services/training/build_prop_edges.py` produces sane projections and edges across 7
  markets with live odds data (pass_att, pass_completions, rush_att, recs, rec_yds, rush_yds;
  pass_yds/pass_td/rush_td/rec_td have working models but no odds coverage in the current
  sample data).
- **The API does not yet expose `prop_edges`.** There is no endpoint that serves the edge
  table, and the frontend has no concept of a sportsbook line, an edge, or a recommendation.
  The only projection path the frontend actually calls
  (`GET /players/{id}/projection_ml`) is a separate, simpler code path that returns a raw
  model number with no odds comparison at all. **Closing this gap is the current top
  priority** — see `docs/API.md` and `docs/FRONTEND.md`.
- The frontend (`apps/web`) is a 2-page skeleton (player search + player detail) built
  before most of the current feature/model work; it needs a dedicated pass.

## Architecture

### Data plane

- `jobs/ingestion/` — the real nflverse ingestion pipeline (`app/etl/nflverse_ingest.py`,
  ~1600 lines) that populates `player_game_stats_app`, `nfl_games`, `snap_counts`,
  `ff_opportunity`, `injuries`, `depth_charts`, NGS tables, etc. Not currently wired into
  `docker-compose.yml` — run it standalone (see `docs/ARCHITECTURE.md`).
- `services/api/app/routes/jobs.py` — `POST /api/v1/jobs/build_features` and
  `/jobs/attach_labels`, the feature-engineering step that turns raw game logs into rolling
  + contextual features per market, stored in `player_market_features`.

### ML plane

- `services/training/train.py` — trains one RandomForest (or GradientBoosting) per market,
  filtered to `prop_markets.eligible_positions`, writes a `.joblib` + `.json` artifact and
  updates `trained_models`/`active_models`.
- `services/training/eval.py` — honest held-out evaluation: time-ordered split, position
  filter from the DB (not hardcoded), bias check, lift vs. a naive `weighted_mean` baseline.
- `services/training/build_prop_edges.py` — loads sportsbook props, matches each to the
  player's most recent feature row, predicts, blends with the rolling `weighted_mean`,
  compares to the sportsbook line, computes win probability/edge/tier, writes `prop_edges`.

### Serving plane

- `services/api/` — FastAPI. Player search/detail, feature-building jobs, Odds API sync,
  and the raw `projection_ml`/`projection_baseline` endpoints (see the gap noted above).
- `services/inference/` — intentionally still a placeholder (health check only); a future
  extension point if inference needs to scale independently of `services/api`.

### UI

- `apps/web/` — React + Vite. `PlayersSearch.tsx`, `PlayerDetail.tsx`.

## Current model performance

`rf_posfilt_v4`, evaluated with `eval.py` (time-ordered held-out split, positions filtered
to `prop_markets.eligible_positions`, bias = mean(prediction - actual)):

| Market | R² | Bias | vs. weighted_mean baseline |
|---|---|---|---|
| rush_att | 0.73 | -0.06 | beats baseline |
| rush_yds | 0.59 | -0.16 | beats baseline |
| recs | 0.42 | +0.10 | beats baseline |
| pass_att | 0.40 | +0.88 | beats baseline |
| rec_yds | 0.37 | +0.63 | beats baseline |
| pass_completions | 0.35 | +1.09 | beats baseline |
| pass_yds | 0.35 | +10.1 | beats baseline |
| rush_td | 0.15 | 0.00 | beats baseline |
| pass_td | 0.11 | +0.06 | beats baseline |
| rec_td | 0.10 | 0.00 | beats baseline |

TD-count markets are expected to have low R² — touchdowns are rare, near-binary events
poorly suited to plain regression (a Poisson/count model would likely do better; not yet
tried). Every market beats the naive rolling-average baseline. Volume/yardage markets sit
around R² 0.35-0.6, which is in the range typically reported for weekly NFL prop prediction
from pre-game features; rushing volume is more predictable than passing/receiving because
carry share for a given back tends to be more scheduled game-to-game.

Feature set per market: rolling `mean`/`stddev`/`weighted_mean`/`trend` (lookback=5 games),
market-specific engineered features (target share, yards per target/carry/attempt, opponent
defensive rates), rolling snap share (`snap_counts.offense_pct`), nflverse's own
`ff_opportunity` expected-usage model, and the **current game's** pre-game Vegas
spread/total/weather from `nfl_games` (not a rolling average — the actual context for the
game being predicted, known before kickoff, same information a sportsbook line is priced
from).

## Local development

Start Docker Desktop, then:

```bash
docker compose up -d postgres redis
docker compose build api training
docker compose up -d api
```

Build features + train a market (see `docs/runbooks/local-dev.md` for the full sequence):

```bash
curl -X POST "http://localhost:8000/api/v1/jobs/build_features?market_code=rec_yds&lookback=5"
curl -X POST "http://localhost:8000/api/v1/jobs/attach_labels?market_code=rec_yds&lookback=5"
docker compose run --rm -e MARKET_CODE=rec_yds -e MODEL_NAME=rf_posfilt_v4 -e LOOKBACK=5 training python train.py
docker compose run --rm -e MARKET_CODE=rec_yds -e MODEL_NAME=rf_posfilt_v4 -e LOOKBACK=5 training python eval.py
docker compose run --rm training python build_prop_edges.py
```

Model artifacts are **not tracked in git** (`.gitignore` excludes `artifacts/`) — they're a
local/deployment concern. Retrain from the sequence above to regenerate them.

## Repo layout

- `services/training/` — the real training/eval/edge pipeline. Most development happens here.
- `services/api/` — FastAPI backend.
- `services/inference/` — placeholder for a future dedicated inference service.
- `jobs/ingestion/` — real, standalone nflverse ingestion pipeline.
- `apps/web/` — React frontend.
- `db/migrations/`, `db/views/` — schema and feature-view SQL, still relevant.
- `db/bootstrap/`, `db/backfills/` — one-off setup/repair SQL, some historical.
- `db/inspection/` — ad-hoc diagnostic queries, kept as debugging reference.

See `docs/ARCHITECTURE.md`, `docs/ML_PIPELINE.md`, and `docs/API.md` for detail.

## Philosophy

Ground fixes in real data before changing code. The rec_yds "overprojection" problem that
blocked this project for months turned out to be two plumbing bugs (a stale row-matching
bug in the edge builder, and a null `team` column silently zeroing out every market except
rec_yds) plus a target transform that was making things worse — not the model. Don't add
clamps or magic constants to paper over a symptom; find the actual cause.

## Author

**Seid Cubro** — LinkedIn: [seid-cubro](https://www.linkedin.com/in/seid-cubro) —
[seidcubro.vercel.app](https://seidcubro.vercel.app)
