
# Player Prop Machine Learning Analysis Platform

A full-stack, production-style analytics platform for NFL **player prop projections**, combining:
- statistical baselines,
- market-specific feature engineering,
- machine learning training + artifact management,
- a FastAPI backend,
- and a React + Vite frontend.

The codebase is organized as a monorepo to reflect how real teams split **jobs** (batch) from **services** (online).

## Repository layout

- `apps/web/` — React frontend
- `services/api/` — FastAPI backend (serves projections and player data)
- `services/inference/` — inference microservice scaffold
- `services/training/` — training utilities/service (writes model artifacts)
- `jobs/*/` — batch jobs (ingestion, ETL, features, training)
- `libs/common_python/` — shared Python utilities (scaffold)
- `infra/terraform/` — infrastructure as code scaffolding
- `deploy/` — Kubernetes/Helm scaffolding
- `docs/` — architecture + runbooks + specs

## Quickstart (local)

Start the local stack:

```bash
docker compose up --build
```

Health check:

```bash
curl http://localhost:8000/health
```

Run the web app:

```bash
cd apps/web
npm install
npm run dev
```

## Documentation

- Architecture: `docs/ARCHITECTURE.md`
- Development guide: `docs/DEVELOPMENT.md`
- API overview: `docs/API.md`
- ML pipeline: `docs/ML_PIPELINE.md`
- Frontend: `docs/FRONTEND.md`

## Quality standards

- All public/exported code should have docstrings (Python) or JSDoc (TypeScript).
- Prefer type hints and small pure functions for testability.
- Avoid side effects at import time.

## License

See `LICENSE`.
