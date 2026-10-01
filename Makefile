# Every target runs inside Docker; only Docker + make are needed on the host.
COMPOSE ?= docker compose
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

.PHONY: help setup dev dev-api dev-web build-web migrate seed freeze-prompts mcp-tools graph demo e2e test test-api test-web lint down clean

help:
	@echo "setup    build images, create .env and .data/"
	@echo "dev      run API and web together (web on http://localhost:5173, API docs on :8000/docs)"
	@echo "dev-api  run only the FastAPI server with reload on http://localhost:8000 (docs at /docs)"
	@echo "dev-web  run the Vite dev server (also starts the API it proxies to)"
	@echo "build-web  typecheck and build the production bundle"
	@echo "migrate  apply Alembic migrations"
	@echo "seed     (re)create demo data: WIPES existing rows, then loads 2 boards / 30 cards"
	@echo "freeze-prompts  record hashes of new prompt versions so they can no longer be edited"
	@echo "mcp-tools  start every MCP server and list all their tools"
	@echo "graph    run the triage workflow: make graph ARGS='--card-id 1 --steps analyze,score'"
	@echo "demo     add sample cards and human decisions (mock mode) so the metrics page has data; run after seed"
	@echo "e2e      browser test of the triage flow in mock mode (own database and ports; your data is untouched)"
	@echo "test     run the backend (pytest) and frontend (vitest) tests; test-api / test-web for one"
	@echo "lint     run ruff and the TypeScript typecheck"
	@echo "down     stop containers"
	@echo "clean    stop containers and delete the local database (.data/)"

.data:
	mkdir -p .data

setup: | .data
	@test -f .env || cp .env.example .env
	$(COMPOSE) build api web

dev: | .data
	$(COMPOSE) up --build api web

dev-api: | .data
	$(COMPOSE) up --build api

dev-web: | .data
	$(COMPOSE) up --build web

build-web:
	$(COMPOSE) run --rm --no-deps web npm run build

migrate: | .data
	$(COMPOSE) run --rm api alembic upgrade head

seed: | .data
	$(COMPOSE) run --rm api sh -c "alembic upgrade head && python -m data.seed.seed --reset"

freeze-prompts:
	$(COMPOSE) run --rm --no-deps api python -m ai.llm.registry freeze

mcp-tools:
	$(COMPOSE) run --rm --no-deps api python -m ai.mcp_servers.list_tools

graph: | .data
	$(COMPOSE) run --rm --no-deps api python -m ai.graph.run $(ARGS)

demo: | .data
	$(COMPOSE) run --rm api python -m data.seed.demo $(ARGS)

# The end-to-end test runs in a separate Compose project with its own database, ports and checkpoint
# file, in MOCK_AI mode, so it never touches your dev data or calls a real model.
E2E_ENV = COMPOSE_PROJECT_NAME=mectacto-e2e MOCK_AI=1 API_PORT=8100 WEB_PORT=5273 \
	DATABASE_URL=sqlite:////data/e2e.db GRAPH_CHECKPOINT_PATH=/data/e2e-checkpoints.db

e2e: | .data
	rm -f .data/e2e.db* .data/e2e-checkpoints.db*
	$(E2E_ENV) $(COMPOSE) run --rm api sh -c "alembic upgrade head && python -m data.seed.seed --reset"
	$(E2E_ENV) $(COMPOSE) up -d --build --wait api web
	mkdir -p e2e/out
	docker run --rm --network host -e E2E_URL=http://localhost:5273 -v "$(CURDIR)/e2e:/e2e" -w /e2e \
		mcr.microsoft.com/playwright:v1.49.0-noble sh -c "npm i playwright@1.49.0 --no-audit --no-fund >/dev/null 2>&1 && node triage-flow.mjs"; \
		status=$$?; $(E2E_ENV) $(COMPOSE) down >/dev/null 2>&1; exit $$status

test: test-api test-web

test-api:
	$(COMPOSE) run --rm --no-deps api pytest $(ARGS)

test-web:
	$(COMPOSE) run --rm --no-deps web npm test

lint:
	$(COMPOSE) run --rm --no-deps api ruff check .
	$(COMPOSE) run --rm --no-deps web npm run typecheck

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v
	rm -rf .data
