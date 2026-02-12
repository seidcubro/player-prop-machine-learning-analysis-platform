
# API

> This document describes the HTTP API exposed by `services/api`.

## Base URL (local)

- `http://localhost:8000`

## Health

- `GET /health` → `{ status: "ok", service: "api" }`

## Players

- `GET /players?limit=50&offset=0`
  - Returns a paginated list of players.

- `GET /players/{player_id}`
  - Returns a single player record.

## Projections (baseline + ML)

Baseline and ML endpoints are implemented in `services/api/app/routes/players.py`.
The web client calls these from `apps/web/src/api.ts`.

Typical query parameters:
- `market_code`: market identifier (e.g., `pass_yds`, `rec_yds`, etc.)
- `lookback`: number of recent games to consider

Notes:
- ML projection endpoints require **trained artifacts** to be present in the mounted artifacts directory.
