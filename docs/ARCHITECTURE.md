
# Architecture

## Overview

This repository is a **monorepo** for a production-style NFL player prop analytics platform.
It is organized around a standard data/ML lifecycle:

1. **Ingestion** – pull raw data and load it into the database
2. **Feature engineering** – create market-specific feature sets
3. **Training** – train and persist ML artifacts per market/model
4. **Inference / API** – serve projections to consumers
5. **Frontend** – visualize players, markets, and projection details

## High-level components

### Data plane (batch)

- `jobs/ingestion/`: ingest raw NFL/player/game data into Postgres
- `jobs/features/`: build market-specific feature tables (planned/placeholder)
- `jobs/training/`: train models in batch (planned/placeholder)
- `services/training/`: training service/script used for local/dev training and artifact output

### Serving plane (online)

- `services/api/`: FastAPI HTTP API consumed by the React frontend
- `services/inference/`: placeholder inference microservice for future scaling

### UI

- `apps/web/`: React + Vite frontend

### Infrastructure

- `docker-compose.yml`: local dev stack (Postgres, Redis, API, inference, training)
- `infra/terraform/`: Terraform environments (dev/prod)
- `deploy/k8s/`: Kustomize overlays (currently scaffold)
- `deploy/helm/`: Helm scaffolding (README only)

## Data storage

- **Postgres** is the primary datastore for player and historical stat tables.
- **Redis** is available for caching and/or job coordination in local dev (future use).

## Artifacts

Training artifacts are written to a shared artifacts directory:

- Local/dev: `services/training/artifacts/`
- Mounted into API container as read-only via `docker-compose.yml`

Artifacts typically include:
- serialized model object (e.g., `joblib`)
- metadata JSON (training config, feature columns, model version, metrics)

## Conventions

- Keep services/jobs **stateless** and driven by configuration.
- Prefer **pure functions** in feature engineering/training to support unit tests.
- All public functions/classes should have docstrings/JSDoc.
