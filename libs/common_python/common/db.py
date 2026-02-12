"""Shared database helpers (optional shared library).

This repository is structured as a multi-service monorepo. The `libs/common_python`
package is intended to hold reusable, dependency-light helpers that can be shared
across jobs/services (API, ingestion, training, inference).

Database utilities are currently implemented independently inside each component
(for simplicity during iteration). If you want to centralize behavior, move
common logic here, such as:
- parsing DATABASE_URL / POSTGRES_* env vars,
- creating SQLAlchemy engines/sessions,
- safe transaction wrappers,
- retry/backoff helpers for transient failures.
"""
