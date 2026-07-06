# API Contract

Last updated: 2026-07-05

This document previously specified an aspirational contract (a `{ok, error}` envelope,
`GET /projections`, job-status polling) that was never implemented. The real, current API
surface is documented in `docs/API.md` — read that instead; this file is kept only as a
pointer so links don't break.

Actual conventions, for reference:
- Success responses are ad-hoc per-route JSON (usually including `"ok": true`), not a
  standardized envelope.
- Errors are plain FastAPI `HTTPException` -> `{ "detail": "message" }` on the wire.
- All routes are mounted under `/api/v1` except `/health`.
- There is no `/v2`; no versioning scheme has been needed yet.
