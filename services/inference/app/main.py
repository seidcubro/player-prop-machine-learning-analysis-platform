"""Inference microservice entrypoint.

This service is intended to expose model inference as a standalone HTTP service
(separate from the main API), making it easier to scale and evolve inference
independently (e.g., multiple model versions, GPU workers, etc.).

Currently:
- Provides a health endpoint and a placeholder structure for future inference APIs.
"""

from fastapi import FastAPI

app = FastAPI(title="Player Prop Inference", version="0.1.0")

@app.get("/health")
def health():
    """Health check endpoint for the inference service.

    Returns:
        dict: `{"status": "ok", "service": "inference"}`.
    """
    return {"status": "ok", "service": "inference"}
