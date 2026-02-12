"""Inference service configuration (future extension point).

The inference service is currently a minimal FastAPI skeleton.

When you split inference out of the API, configuration commonly includes:
- artifact root / S3 bucket + prefix
- model cache settings (size, TTL)
- concurrency and worker configuration
- observability configuration (logging, tracing)

This file documents the intended direction so the service remains easy to evolve.
"""
