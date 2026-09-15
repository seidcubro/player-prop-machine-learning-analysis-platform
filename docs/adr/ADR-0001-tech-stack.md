# ADR-0001: Tech stack

Status: Accepted
Date: 2026-07-05 (written retroactively, the stack was already in place; this documents
why, since the decision was never recorded)

## Context

PriorLine needed a stack that a single developer could run entirely locally (Windows +
Docker Desktop), with a real relational store for a fairly relational feature/model
registry problem (players, markets, feature rows, model versions, edges), and a Python ML
ecosystem for training/evaluation.

## Decision

- **Postgres** as the sole datastore. The domain is naturally relational (players x markets
  x games x lookback windows x model versions), and SQL views/CTEs already do meaningful
  work (e.g. `db/views/create_team_pass.sql`, `create_rec_defense_fixed.sql`) computing
  team-level aggregates the Python feature builder joins against.
- **FastAPI** for the API layer, async-capable, automatic OpenAPI, minimal boilerplate for
  the CRUD-plus-jobs shape this needs.
- **scikit-learn (RandomForest/GradientBoosting)** for models, not a deep learning framework
 , the feature set is small/tabular (dozens of engineered columns, tens of thousands of
  rows per market), which is squarely tree-ensemble territory. Tried both; RandomForest and
  GradientBoosting perform comparably (see `docs/ML_PIPELINE.md`), so RandomForest is the
  default for its lower tuning sensitivity.
- **Docker Compose** (not Kubernetes) for local orchestration, this is a single-developer,
  single-machine project. `infra/terraform/`, `deploy/k8s/`, `deploy/helm/` were removed as
  empty scaffolding for a deployment target that doesn't exist yet; if/when this actually
  gets deployed somewhere, build real infra-as-code against whatever that target is, rather
  than resurrecting the placeholder.
- **React + Vite** for the frontend, minimal, fast local dev loop; no heavier framework
  needed for eight pages with no auth and no server rendering.
- **Redis** was provisioned and never used by any service, so it is gone as of
  2026-09-15. The API's rate limiter keeps its counters in process, which is correct
  for a single container, and nothing else ever asked for a cache or a queue. If one
  of those is needed later, adding it back is an afternoon.
- No horizontal scaling story yet (single Postgres instance, no read replicas, no queue).
  Not a concern at 1.3M rows and a read-only public site: the heavy work is a weekly batch,
  not a request path, and the largest table nobody queries live.
- `services/inference` used to exist as a placeholder in case inference ever needed to
  scale independently of the API. Removed on 2026-09-15: it held a health endpoint and
  nothing else, and an empty service costs more in explanation than it saves in future
  work. Inference runs inside the API. Redis went with it, having never had a client.
