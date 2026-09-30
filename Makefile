# Every target runs inside Docker; only Docker + make are needed on the host.
COMPOSE ?= docker compose
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

.PHONY: help setup dev dev-api dev-web build-web migrate seed freeze-prompts test test-api test-web lint down clean

help:
	@echo "setup    build images, create .env and .data/"
	@echo "dev      run API and web together (web on http://localhost:5173, API docs on :8000/docs)"
	@echo "dev-api  run only the FastAPI server with reload on http://localhost:8000 (docs at /docs)"
	@echo "dev-web  run the Vite dev server (also starts the API it proxies to)"
	@echo "build-web  typecheck and build the production bundle"
	@echo "migrate  apply Alembic migrations"
	@echo "seed     (re)create demo data: WIPES existing rows, then loads 2 boards / 30 cards"
	@echo "freeze-prompts  record hashes of new prompt versions so they can no longer be edited"
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
