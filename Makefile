# GigBid developer commands. Run `make help` for the list.
# On Windows, run these inside WSL2 (Ubuntu), where make and docker are available.

PY ?= uv run python
PYTEST ?= uv run pytest

.PHONY: help install dev api web api-client gigsim test lint fmt eval seed world personas data-download data-prep devset demo models compose-up compose-down

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

install: ## Install Python dependencies from the lockfile
	uv sync --all-extras

dev: ## Run GigSim and the API locally with auto-reload (two processes)
	$(MAKE) -j2 gigsim api

api: ## Run the API on :8000
	uv run uvicorn apps.api.main:app --reload --port 8000

web: ## Run the Next.js workspace on :3000 after npm ci
	npm --prefix apps/web run dev

api-client: ## Regenerate the web client's OpenAPI contract
	$(PY) -m apps.api.export_openapi --output apps/web/openapi.json
	npm --prefix apps/web run gen:api

gigsim: ## Run the GigSim simulator on :8081
	uv run uvicorn gigsim.app:app --reload --port 8081

test: ## Unit, contract and integration tests
	$(PYTEST) -q

lint: ## Ruff lint
	uv run ruff check .

fmt: ## Ruff format
	uv run ruff format .

models: ## Print which model each node uses under the active profile
	$(PY) -c "from agent.llm import describe_routing; import json; print(json.dumps(describe_routing(), indent=2))"

data-download: ## Download the Kaggle datasets into data/raw (needs ~/.kaggle/kaggle.json)
	$(PY) -m scripts.data.download

data-prep: ## Clean, dedupe and tag Kaggle data; build market statistics
	$(PY) -m scripts.data.preprocess

personas: ## Render the three persona corpora from their bibles (template mode; add ARGS=--llm)
	$(PY) -m scripts.personas.render $(ARGS)

world: ## Build the GigSim world and the dev set from processed data and personas
	$(PY) -m scripts.data.build_world

seed: ## Load the world into GigSim (GigSim must be running)
	$(PY) -m gigsim.seed --world $${GIGSIM_WORLD_PATH:-data/processed/gigsim_world}

eval: ## Run the offline smoke evaluation and regenerate report tables
	$(PY) -m evals.runners.smoke --jobs 30 --gate
	$(PY) -m scripts.make_tables

demo: ## End-to-end demo against GigSim (Phase 2+)
	$(PY) -m scripts.single_agent_demo

compose-up: ## Start everything with docker compose
	docker compose -f infra/docker-compose.yml --env-file .env up --build

compose-down:
	docker compose -f infra/docker-compose.yml down
