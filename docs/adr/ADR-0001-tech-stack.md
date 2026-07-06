# ADR-0001: Tech stack

Status: Accepted
Date: 2026-07-05 (written retroactively — the stack was already in place; this documents
why, since the decision was never recorded)

## Context

PropSignal needed a stack that a single developer could run entirely locally (Windows +
Docker Desktop), with a real relational store for a fairly relational feature/model
registry problem (players, markets, feature rows, model versions, edges), and a Python ML
ecosystem for training/evaluation.

## Decision

- **Postgres** as the sole datastore. The domain is naturally relational (players x markets
  x games x lookback windows x model versions), and SQL views/CTEs already do meaningful
  work (e.g. `db/views/create_team_pass.sql`, `create_rec_defense_fixed.sql`) computing
  team-level aggregates the Python feature builder joins against.
- **FastAPI** for the API layer — async-capable, automatic OpenAPI, minimal boilerplate for
  the CRUD-plus-jobs shape this needs.
- **scikit-learn (RandomForest/GradientBoosting)** for models, not a deep learning framework
  — the feature set is small/tabular (dozens of engineered columns, tens of thousands of
  rows per market), which is squarely tree-ensemble territory. Tried both; RandomForest and
  GradientBoosting perform comparably (see `docs/ML_PIPELINE.md`), so RandomForest is the
  default for its lower tuning sensitivity.
- **Docker Compose** (not Kubernetes) for local orchestration — this is a single-developer,
  single-machine project. `infra/terraform/`, `deploy/k8s/`, `deploy/helm/` were removed as
  empty scaffolding for a deployment target that doesn't exist yet; if/when this actually
  gets deployed somewhere, build real infra-as-code against whatever that target is, rather
  than resurrecting the placeholder.
- **React + Vite** for the frontend — minimal, fast local dev loop; no heavier framework
  needed for what's currently a 2-page app.
- **Redis** is provisioned but not yet used by any service — kept available for future
  caching/job-coordination needs.

## Consequences

- No horizontal scaling story yet (single Postgres instance, no read replicas, no queue).
  Not a concern at current data volume (~50k rows across the core tables) or usage (one
  user).
- `services/inference` exists as an intentional placeholder in case inference ever needs to
  scale independently of the API — not built out, and shouldn't be until there's an actual
  need (see `docs/ARCHITECTURE.md` "History" for why speculative scaffolding elsewhere in
  this repo was removed rather than kept).
