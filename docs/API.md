# API

This document describes the HTTP API exposed by `services/api`. Base path for all routes
below (except `/health`) is `/api/v1`.

## Base URL (local)

`http://localhost:8000`

## Health

- `GET /health` -> `{ "status": "ok", "service": "api" }`

## Players (`services/api/app/routes/players.py`)

- `GET /players?search=&limit=50&offset=0&include_total=false`
  Paginated player search. `search` does an `ILIKE` match across name/team/position/
  external_id. -> `{ "ok": true, "players": [...], "total"?: n }`

- `GET /players/{player_id}`
  Single player by internal `id`. -> `{ "ok": true, "player": {...} }` or 404.

- `GET /players/{player_id}/projection_ml?market_code=rec_yds&lookback=5`
  Loads the `active_models` artifact for the market, builds a feature vector from the
  player's single most recent `player_market_features` row, runs the model, persists the
  result to `ml_projections`, returns the raw prediction. **No sportsbook comparison, no
  edge, no `target_transform` un-transform** — this is a separate, simpler code path than
  `services/training/build_prop_edges.py`. See `docs/specs/ml-design.md` "Inference
  contract" for the full gap description.

- `GET /players/{player_id}/projection_history?market_code=&model_name=&lookback=&limit=20`
  Previously generated `ml_projections` rows for a player.

- `GET /players/{player_id}/projection_baseline?market_code=&model_name=baseline_v1`
  Latest stored baseline projection (if one has been generated).

## Feature-building jobs (`services/api/app/routes/jobs.py`)

- `POST /jobs/build_features?market_code=rec_yds&lookback=5`
  Computes rolling + contextual features for every eligible-position player/game and
  upserts into `player_market_features`. Real response:
  `{ "ok": true, "market_code": "...", "market_id": n, "stat_field": "...", "feature_family": "...", "lookback": 5, "eligible_positions": [...], "upstream_features_used": [...], "upserts": n }`

- `POST /jobs/attach_labels?market_code=rec_yds&lookback=5`
  Fills `label_actual` for rows whose target game has since been played.
  `{ "ok": true, "market_code": "...", "market_id": n, "stat_field": "...", "updated": n }`

## Odds sync (`services/api/app/routes/odds.py`)

All hit The Odds API (`ODDS_API_KEY` in `.env`) and upsert into `odds_events`/
`odds_player_props`:

- `POST /odds/sync/events` — upcoming events for the configured sport.
- `POST /odds/sync/player_props` — player prop odds for events already in `odds_events`.
- `POST /odds/sync/historical_events?date=...` — historical events snapshot for a date.
- `POST /odds/sync/historical_player_props?date=...` — historical player props for a date.

## Edges (`services/api/app/routes/edges.py`)

The route that connects the edge pipeline to consumers (`build_prop_edges.py` writes
`prop_edges`; these routes read it):

- `GET /edges?market_code=&min_tier=&side=&search=&sort=&order=&limit=&offset=`
  Filterable/sortable/paginated list of computed edges. `min_tier` is inclusive-upward
  (`strong` returns strong + elite). Sort keys: `edge` (|raw_edge|, default), `win_prob`,
  `line`, `projection`, `commence_time`, `player_name`.
  -> `{ "ok": true, "total": n, "limit": n, "offset": n, "edges": [...] }`

- `GET /edges/summary` — per-market counts/avg edge, per-tier counts, last run timestamp.
  Used by the frontend dashboard header.

## Error handling

Routes use plain FastAPI `HTTPException(status_code, detail=...)` — the actual error shape
on the wire is `{ "detail": "message" }`, not a custom envelope. (An earlier spec doc
described a `{ "ok": false, "error": {...} }` envelope that was never implemented — see
`docs/specs/api-contract.md`.)
