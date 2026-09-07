"""FastAPI application factory / entrypoint.

This service exposes HTTP endpoints for:
- listing and retrieving players
- retrieving baseline and ML projections
- triggering/inspecting background jobs (where implemented)
- health checks

The API is intended to be consumed by the React web frontend and by internal tooling.

Operational notes:
- CORS is enabled for local Vite development.
- Database connectivity is provided via `services/api/app/db.py`.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router
from app.routes.odds import router as odds_router

app = FastAPI(title="Player Prop API", version="0.1.0")

# Browser origins allowed to call this API.
#
# This was a hardcoded pair of localhost entries, which is correct for a laptop
# and fatal for a deployment: the browser blocks every call from the hosted
# frontend and the site comes up empty with no server-side error to find. The
# deployed origin goes in WEB_ORIGINS as a comma-separated list.
#
# Wildcards are deliberately not supported. `allow_credentials=True` with
# `allow_origins=["*"]` is rejected by browsers anyway, and an API that can
# spend money on the Odds account should not be callable from any page on the
# internet.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
_EXTRA_ORIGINS = [
    o.strip() for o in os.getenv("WEB_ORIGINS", "").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS + _EXTRA_ORIGINS,
    # Vercel gives every deployment its own subdomain, so preview builds would
    # each need adding by hand. This matches them by pattern instead, while
    # still refusing arbitrary origins.
    allow_origin_regex=os.getenv("WEB_ORIGIN_REGEX") or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(odds_router, prefix="/api/v1")

@app.get("/health")
def health():
    """Health check endpoint.

    Returns a minimal payload used by local dev tooling, containers, and
    orchestrators (Docker Compose / Kubernetes) to determine whether the API
    process is up and able to serve requests.

    Returns:
        dict: `{"status": "ok", "service": "api"}`.
    """
    return {"status": "ok", "service": "api"}
