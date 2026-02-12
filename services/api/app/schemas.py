"""API schemas (optional).

FastAPI pairs nicely with Pydantic models for request/response validation.

This project currently returns plain dictionaries for simplicity and speed during
iteration. When you want stronger typing and cleaner OpenAPI docs, define
Pydantic models here (e.g., PlayerOut, ProjectionOut, MLProjectionOut) and use
`response_model=...` on endpoints.

This module is intentionally documented and lightweight to make the future
migration path obvious to anyone reading the codebase.
"""
