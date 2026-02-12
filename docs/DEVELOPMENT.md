
# Development Guide

## Prerequisites

- Docker Desktop (or Docker Engine)
- Python 3.11+ (for local tooling)
- Node.js 18+ (for the web app)

## Local stack (recommended)

Start the stack:

```bash
docker compose up --build
```

Services:
- API: http://localhost:8000
- Inference: http://localhost:8001
- Postgres: localhost:5432
- Redis: localhost:6379

Health check:

```bash
curl http://localhost:8000/health
```

## Web app

From `apps/web`:

```bash
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000` (see `apps/web/src/api.ts`).

## Formatting & linting

Python:
- Ruff is the default linter/formatter target (see repo `pyproject.toml`).

TypeScript:
- Use the existing Vite/TS toolchain; add ESLint/TypeDoc as needed.

## Common troubleshooting

- **CORS**: API enables local dev origins by default.
- **No projections**: ensure training artifacts exist and are mounted into the API container.
- **DB empty**: run ingestion job to populate `players` and other core tables.
