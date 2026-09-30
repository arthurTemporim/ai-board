# Every target runs inside Docker; only Docker + make are needed on the host.
COMPOSE ?= docker compose
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

.PHONY: help setup dev-api migrate seed test lint down clean

help:
	@echo "setup    build images, create .env and .data/"
	@echo "dev-api  run the FastAPI server with reload on http://localhost:8000 (docs at /docs)"
	@echo "migrate  apply Alembic migrations"
	@echo "seed     (re)create demo data: WIPES existing rows, then loads 2 boards / 30 cards"
	@echo "test     run pytest"
	@echo "lint     run ruff"
	@echo "down     stop containers"
	@echo "clean    stop containers and delete the local database (.data/)"

.data:
	mkdir -p .data

setup: | .data
	@test -f .env || cp .env.example .env
	$(COMPOSE) build api

dev-api: | .data
	$(COMPOSE) up --build api

migrate: | .data
	$(COMPOSE) run --rm api alembic upgrade head

seed: | .data
	$(COMPOSE) run --rm api sh -c "alembic upgrade head && python -m data.seed.seed --reset"

test:
	$(COMPOSE) run --rm --no-deps api pytest $(ARGS)

lint:
	$(COMPOSE) run --rm --no-deps api ruff check .

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v
	rm -rf .data
