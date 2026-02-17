# API Contract (Human-Readable)

Last updated: 2026-02-16
Base path: /api/v1

Principles:
- All successful responses SHOULD include ok: true.
- Errors SHOULD use a consistent envelope: ok: false with an error object.
- Pagination MUST be server-side for league-scale datasets.
- All new endpoints MUST follow this contract unless versioned.

---

## 1. Common Response Shapes

### 1.1 Success Envelope

Most new endpoints SHOULD return:

{
  "ok": true,
  "data": {}
}

Legacy endpoints may return top-level fields (e.g., players, total).

---

### 1.2 Error Envelope (Standard)

All endpoints MUST return this on failure:

{
  "ok": false,
  "error": {
    "code": "string_machine_readable",
    "message": "human readable summary",
    "details": {}
  }
}

Suggested error codes:

- validation_error
- not_found
- conflict
- db_error
- internal_error
- unsupported_market
- insufficient_data
- artifact_missing

HTTP mapping:
- 400 -> validation_error
- 404 -> not_found
- 409 -> conflict
- 500 -> internal_error

---

## 2. Players

### 2.1 List Players

GET /players

Query Parameters:
- search (string, optional)
- limit (int, optional)
- offset (int, optional)
- include_total (bool, optional)

Search behavior:
ILIKE across:
- first_name || ' ' || last_name
- first_name
- last_name
- team
- position
- external_id

Sorting:
ORDER BY last_name, first_name

Pagination:
LIMIT :limit OFFSET :offset

Response:

{
  "ok": true,
  "players": [
    {
      "id": 123,
      "first_name": "Jaylen",
      "last_name": "Warren",
      "position": "RB",
      "team": null,
      "external_id": "00-003xxxx"
    }
  ],
  "total": 42
}

Notes:
- team may be null.
- external_id is nflverse identifier.

---

### 2.2 Get Player By ID

GET /players/{player_id}

Success:

{
  "ok": true,
  "player": {
    "id": 123,
    "first_name": "Jaylen",
    "last_name": "Warren",
    "position": "RB",
    "team": "PIT",
    "external_id": "00-003xxxx"
  }
}

Failure:

{
  "ok": false,
  "error": {
    "code": "not_found",
    "message": "Player not found",
    "details": { "player_id": 123 }
  }
}

---

## 3. Projections (Market-Based)

Markets:
- rec_yds
- rush_yds
- pass_yds
- receptions
- tds

### 3.1 Get Projection

GET /projections

Query:
- player_id (int, required)
- market (string, required)
- model (string, optional, default ridge_v1)
- lookback (int, optional)

Success:

{
  "ok": true,
  "projection": {
    "player_id": 123,
    "market": "rec_yds",
    "model": "ridge_v1",
    "lookback": 5,
    "value": 47.3,
    "units": "yards",
    "generated_at": "2026-02-16T21:00:00Z",
    "metadata": {
      "games_used": 5,
      "position": "RB"
    }
  }
}

Unsupported market:

{
  "ok": false,
  "error": {
    "code": "unsupported_market",
    "message": "Market rec_yds is not supported for position K",
    "details": {
      "position": "K",
      "market": "rec_yds"
    }
  }
}

Insufficient data:

{
  "ok": false,
  "error": {
    "code": "insufficient_data",
    "message": "Not enough historical games to generate projection",
    "details": {
      "games_available": 1,
      "games_required": 5
    }
  }
}

---

## 4. Jobs

Jobs orchestrate:
- ingestion
- feature building
- labeling
- training

Success:

{
  "ok": true,
  "job": {
    "name": "ingestion",
    "status": "started",
    "started_at": "2026-02-16T20:00:00Z"
  }
}

---

## 5. Versioning

- All routes under /api/v1
- Breaking changes require /api/v2
- OpenAPI snapshot MUST be updated on change

---

## 6. Non-Negotiables

- No client-side filtering of full datasets
- Projection failures must be explicit
- Models must declare metadata
- API contract must match OpenAPI snapshot

