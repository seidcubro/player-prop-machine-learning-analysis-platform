FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Both PostgreSQL drivers, for the same reason the training image carries both.
#
# This container reads DATABASE_URL from the environment and the server supplies
# postgresql+psycopg://, which is the psycopg 3 dialect. With only psycopg2
# installed SQLAlchemy raises ModuleNotFoundError the moment it builds an
# engine, so the full nflverse ingest died on its first statement. Every
# morning, for four days, while the board kept publishing on box scores that
# stopped at Week 3, which is a model that cannot see a role change.
#
# The training image hit this exact failure in September and was fixed by
# installing both. Nobody checked whether the other image reading the same
# variable had the same hole. It did.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        nflreadpy \
        pandas \
        pyarrow \
        requests \
        sqlalchemy \
        psycopg2-binary \
        "psycopg[binary]"

# Copy ingestion package to /app/app
COPY jobs/ingestion/app /app/app

# Run as a module so "app.*" imports resolve
CMD ["python", "-m", "app.main"]
