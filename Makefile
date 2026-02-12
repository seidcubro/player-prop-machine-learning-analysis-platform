
# Monorepo convenience targets for local development.
# Keep commands simple and explicit; do not hide complex behavior behind make.

.PHONY: help
help:
	@echo "Targets:"
	@echo "  dev-up        Start local docker-compose stack"
	@echo "  dev-down      Stop local docker-compose stack"
	@echo "  lint          Run repo linting (ruff for Python)"
	@echo "  format        Auto-format Python with ruff"
	@echo "  test          Run Python tests (where implemented)"

.PHONY: dev-up
dev-up:
	docker compose up --build

.PHONY: dev-down
dev-down:
	docker compose down -v

.PHONY: lint
lint:
	python -m pip install -q ruff
	ruff check .

.PHONY: format
format:
	python -m pip install -q ruff
	ruff format .

.PHONY: test
test:
	python -m pytest -q
