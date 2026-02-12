"""Feature engineering job configuration.

Configuration for this component is currently handled via environment variables
directly in the implementation modules.

This file exists so the repo has a consistent, discoverable location for config.
When you want to harden configuration, introduce a Pydantic Settings model here
and validate required settings on startup.

Typical settings you may add:
- DATABASE_URL / POSTGRES_* connection pieces
- season ranges / market codes / lookback windows
- artifact locations (local path or S3 bucket)
"""
