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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router

app = FastAPI(title="Player Prop API", version="0.1.0")

# DEV CORS (browser fetch from Vite)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

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
