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

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.routes import router
from app.routes.odds import router as odds_router

from .db import engine

log = logging.getLogger(__name__)

# A ceiling on how fast anyone can read the board.
#
# The odds are licensed for display, not redistribution. The provider's terms
# permit showing their data in a UI and storing it indefinitely, and forbid
# offering it "through your own API, data feed, downloadable files, or any other
# format intended to serve as a source of raw data for others".
#
# /edges returns bookmaker, line and price for every prop, plus the other books
# in `alts`. Serving that to the site is display. Serving it unmetered to a
# script on a loop is a feed, and the difference is the request rate, not the
# intent. CORS does not help here: it restrains browsers, not curl.
#
# The limit is set well above what the site needs. A page load is a handful of
# calls, so a person never sees this; a harvester walking every market and page
# does.
_RATE_DEFAULT = "60/minute"


def _read_rate() -> str:
    """The configured limit, or the default if it cannot be parsed.

    This is validated here because the failure it prevents is genuinely nasty.
    The value is a `limits` rate string, "60/minute", and a bare number is not
    one. slowapi does not parse it when the Limiter is built; it parses it on
    the first request, inside middleware, where the ValueError is handed to a
    handler that expects a RateLimitExceeded. The result is an API that starts
    cleanly, reports itself healthy for as long as nobody asks it anything, and
    then answers every single read with a 500 and a traceback about a missing
    `detail` attribute.

    `.env.example` shipped `READ_RATE_LIMIT=120`, so copying it to the server,
    which is what the deploy instructions tell you to do, produced exactly that.

    Falling back rather than refusing to start is deliberate. A typo in a
    tuning value should not take the site down, and the log line says what
    happened and what is being used instead.
    """
    raw = os.getenv("READ_RATE_LIMIT", "").strip()
    if not raw:
        return _RATE_DEFAULT
    try:
        parse(raw)
    except ValueError:
        log.error(
            "READ_RATE_LIMIT=%r is not a rate limit string; using %s. "
            "Write it as a count and a period, for example 120/minute.",
            raw, _RATE_DEFAULT,
        )
        return _RATE_DEFAULT
    return raw


_RATE = _read_rate()


def _client_ip(request: Request) -> str:
    """The caller's address, not the proxy's.

    In production Caddy sits in front, so every request arrives from the
    proxy and `request.client.host` is the same value for all of them. Limiting
    on that puts the entire internet in one bucket: the first busy visitor locks
    everyone else out, including the site itself.

    Caddy sets X-Forwarded-For, and the left-most entry is the original client.
    The header is trivially forged, which is why this is only trusted when
    TRUST_PROXY_HEADERS is set, and that is only set where something we control
    is terminating the connection.
    """
    if os.getenv("TRUST_PROXY_HEADERS", "0") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip, default_limits=[_RATE])

# The interactive docs are on by default and can be turned off.
#
# They are worth having on a project like this: the schema is the clearest
# statement of what the API does. The tradeoff is that they also advertise
# POST /odds/sync/player_props, which bills a paid quota per event per market.
# That endpoint is behind a shared secret, rate limited, and returns 503 when
# unconfigured, so publishing its existence is not a hole. It is an invitation,
# and worth being a deliberate choice rather than an accident.
#
# Set API_DOCS=off to serve neither the docs nor the schema.
_DOCS = os.getenv("API_DOCS", "on").strip().lower() not in ("0", "off", "false", "no")

app = FastAPI(
    title="Player Prop API",
    version="0.1.0",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limited(request: Request, exc: RateLimitExceeded):
    """Say what happened, rather than a bare 429 with no explanation."""
    return JSONResponse(
        status_code=429,
        content={
            "ok": False,
            "detail": (
                "Rate limit exceeded. This API backs the PriorLine site; the "
                "odds it returns are licensed for display, not redistribution."
            ),
        },
        headers={"Retry-After": "60"},
    )

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

# Registered before CORS, deliberately.
#
# Starlette applies the last middleware added as the outermost layer. With
# the limiter outside CORS, a 429 returns before the CORS headers are
# attached, and the browser reports a blocked request rather than a rate
# limit. The site then looks broken instead of throttled, which is a worse
# failure and a much harder one to diagnose.
app.add_middleware(SlowAPIMiddleware)

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
    """Is this API able to serve a request, database included?

    It used to answer yes whenever the process was running. Renaming a table
    out from under it left every real endpoint returning 500 while this still
    said ok, which is the worst possible answer: the container healthcheck in
    deploy/docker-compose.prod.yml reads this endpoint, so Docker would call the
    API healthy, `restart: unless-stopped` would never fire, and Caddy would
    keep routing traffic to something that cannot answer any of it.

    A health check that cannot fail is not a health check. This one touches the
    database, because an API that cannot reach Postgres has nothing to serve.
    The query is a constant, so it costs a round trip and no planning.

    503 rather than 500: the service is unavailable rather than broken by the
    request, and that is the code orchestrators act on.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        # The reason is logged, not returned. A health endpoint is reachable by
        # anyone and connection errors carry hostnames and usernames.
        log.warning("health check failed to reach the database: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": "api",
                     "detail": "database unreachable"},
        )
    return {"status": "ok", "service": "api"}
