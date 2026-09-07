"""Shared-secret guard for the endpoints that cost money or rewrite the database.

Four endpoints mutate state and none of them had any protection:

    POST /api/v1/jobs/build_features      rewrites the feature table
    POST /api/v1/jobs/attach_labels       rewrites labels
    POST /api/v1/odds/sync/events         spends Odds API credits
    POST /api/v1/odds/sync/player_props   spends a lot of Odds API credits

That was fine while the only thing that could reach them was a browser on this
laptop. The moment the API is on the public internet it stops being fine:
`sync/player_props` bills my paid quota per event per market, and an unscoped
call is over 2,000 credits. Anyone who found the URL could empty the account in
a loop, and nothing about a read-only projections site suggests the write
endpoints are sitting there unlocked.

This is deliberately the smallest thing that works. No user accounts, no
sessions, no JWT -- one operator, one secret, sent as a header.

**It fails closed.** With `ADMIN_TOKEN` unset every mutating endpoint returns
503, rather than silently reverting to open. An accidentally-unconfigured deploy
should refuse to spend money, not quietly allow the internet to spend it. Read
endpoints are untouched, so a misconfigured deploy still serves the site.
"""

import hmac
import os

from fastapi import Header, HTTPException


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """FastAPI dependency. Raises unless the caller presents the right secret.

    Compared with `hmac.compare_digest` rather than `==` so the check does not
    leak the secret one character at a time through response timing.
    """
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "ADMIN_TOKEN is not configured, so write endpoints are "
                "disabled. Set it in the API environment and send it as the "
                "X-Admin-Token header."
            ),
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=401, detail="Missing or invalid X-Admin-Token header."
        )
