.PHONY: install dev web api worker test test-web test-api test-desktop build lint typecheck up down

install:
	npm install
	cd apps/api && uv sync --all-groups

dev:
	@echo "Run 'make api' and 'make web' in separate terminals."

web:
	npm run dev --workspace=@edgeos/web

api:
	cd apps/api && uv run uvicorn edgeos.main:app --reload --host 0.0.0.0 --port 8000

worker:
	cd apps/api && uv run python -m edgeos.worker

test: test-api test-web test-desktop

test-api:
	cd apps/api && uv run pytest

test-web:
	npm run test --workspace=@edgeos/web

test-desktop:
	npm run test --workspace=@edgeos/desktop

build:
	npm run build --workspace=@edgeos/web

lint:
	npm run lint --workspace=@edgeos/web
	cd apps/api && uv run ruff check .

typecheck:
	npm run typecheck --workspace=@edgeos/web
	npm run typecheck --workspace=@edgeos/desktop
	cd apps/api && uv run mypy edgeos

up:
	docker compose up --build

down:
	docker compose down

