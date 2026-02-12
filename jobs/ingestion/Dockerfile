FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        nflreadpy \
        pandas \
        pyarrow \
        requests \
        sqlalchemy \
        psycopg2-binary

# Copy ingestion package to /app/app
COPY jobs/ingestion/app /app/app

# Run as a module so "app.*" imports resolve
CMD ["python", "-m", "app.main"]
