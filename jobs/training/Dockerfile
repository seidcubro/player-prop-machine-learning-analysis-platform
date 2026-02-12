# TRAINING container image
#
# This Dockerfile builds the TRAINING job image. Jobs are intended to run as
# one-off containers (batch) and should exit with a non-zero status on failure.
#
# Notes:
# - Keep images small and deterministic.
# - Pin dependencies via requirements/lockfiles as the project matures.

FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# As this job stabilizes, prefer a pinned dependency set (requirements.txt/uv/poetry)
# so builds are reproducible across machines and CI.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir pandas pyarrow requests sqlalchemy psycopg2-binary

COPY jobs/training/app /app/app
CMD ["python", "-m", "app.main"]
