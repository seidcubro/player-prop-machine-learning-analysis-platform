"""API router package.

This package contains the HTTP route modules for the FastAPI API service.

Most code should import the composed router via:

    from services.api.app.routes import router

That re-export is provided here for convenience and to keep the application's
import surface stable. The actual composition lives in
`services/api/app/routes/api_router.py`.
"""

from .api_router import router
