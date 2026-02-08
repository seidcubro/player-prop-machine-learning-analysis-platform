# Player Prop Machine Learning Analysis Platform

A full-stack, production-style analytics platform for NFL player prop projections, combining
statistical baselines, machine learning models, and a modern web interface.

This project is designed as a real-world system, not a toy app — with clear separation of
ingestion, feature engineering, model training, inference, and visualization.

---

## What This Is

The Player Prop Machine Learning Analysis Platform:

- Ingests NFL player and game data
- Builds market-specific feature sets (e.g. passing yards, rushing yards)
- Trains and evaluates machine learning models
- Generates baseline and ML projections
- Serves projections via a FastAPI backend
- Displays results in a React + Vite frontend

The system is built to scale by market, model, and season, and to support
historical tracking, backtesting, and future model comparisons.

---

## Core Capabilities

### Backend (FastAPI)
- REST API with versioned routes (`/api/v1`)
- PostgreSQL database
- Baseline statistical projections
- ML projection serving (Ridge regression, extensible)
- Projection history tracking
- Job endpoints for feature building and label attachment

### Machine Learning
- Deterministic feature generation
- Time-aware labeling
- Offline model training (containerized)
- Model artifacts and metadata
- Support for multiple markets and lookback windows

### Frontend (React + Vite)
- Player search and detail pages
- Baseline vs ML projection display
- Graceful handling of missing models
- Projection history table
- Clean API abstraction (`api.ts` as single source of truth)

### Infrastructure
- Dockerized services
- Local development via Docker Compose
- Redis for caching and coordination
- CI pipeline scaffolded
- Terraform and Kubernetes scaffolding for cloud deployment

---

## System Architecture

![System Architecture](docs/architecture/Prop Analysis System Architecture Diagram.png)

High-level flow:

1. NFL data ingestion
2. Normalized storage in PostgreSQL
3. Feature construction per market
4. Label attachment from actual game results
5. Offline model training
6. Inference and projection serving
7. Frontend visualization

---

## Repository Structure

```text
player-prop-machine-learning-analysis-platform/
│
├── services/
│   ├── api/              # FastAPI backend
│   ├── inference/        # ML inference service
│   └── training/         # Offline model training
│
├── jobs/                 # Ingestion, features, training jobs
├── apps/
│   └── web/              # React + Vite frontend
│
├── data/                 # Sample and reference datasets
├── docs/                 # Architecture, specs, proposal
├── infra/                # Terraform infrastructure
├── deploy/               # Kubernetes and Helm configs
├── scripts/              # Local dev utilities
├── docker-compose.yml
└── README.md
```

---

## Database Design (High Level)

Key tables include:

- `players`
- `prop_markets`
- `projections` (baseline)
- `player_market_features`
- `ml_projections`

The schema is designed to:
- Separate features, labels, and predictions
- Preserve historical projections
- Support multiple models and markets
- Enable future backtesting and evaluation

Detailed specs live in `docs/specs/`.

---

## Running Locally

### Prerequisites
- Docker
- Docker Compose
- Node.js (for frontend development)

### Start the stack
```bash
docker compose up --build
```

### Verify services
```bash
curl http://localhost:8000/health
```

Frontend:
```
http://localhost:5173
```

---

## Training a Model (Example)

Example: train a Ridge model for rushing yards with a 5-game lookback.

```
# Build features
Invoke-RestMethod -Method Post `
  "http://localhost:8000/api/v1/jobs/build_features?market_code=rush_yds&lookback=5"

# Attach labels
Invoke-RestMethod -Method Post `
  "http://localhost:8000/api/v1/jobs/attach_labels?market_code=rush_yds"

# Train model
docker compose run --rm `
  -e MARKET_CODE=rush_yds `
  -e LOOKBACK=5 `
  -e MODEL_NAME=ridge_v1 `
  training
```

---

## Current Project State

- Backend: stable
- Frontend: functional and wired
- ML pipeline: proven end-to-end
- Data scope: limited (development phase)

This repository currently represents the platform backbone.
The next phase focuses on real NFL data ingestion, multi-season backfills,
UI polish, and public cloud deployment.

---

## Roadmap

Planned expansions include:

- Full NFL ingestion (multi-season)
- Confidence intervals and uncertainty visualization
- Over/Under line input and expected value calculation
- Multi-model comparison and leaderboards
- Backtesting dashboards
- Public cloud hosting
- Authentication and rate limiting

---

## Documentation

Additional documentation is available under `docs/`:

- `docs/proposal/` – Project proposal and feasibility analysis
- `docs/architecture/` – System and data flow diagrams
- `docs/specs/` – API, data model, ML design
- `docs/runbooks/` – Local dev and deployment guides
- `docs/adr/` – Architecture decision records

---

## Disclaimer

This project is for educational and analytical purposes only.
It is not intended for real-money betting or wagering decisions.

---

## Author

Built as a full-stack systems and machine learning project to explore
sports analytics, data engineering, and production-grade ML workflows.


