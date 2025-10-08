.PHONY: run-bot run-api fmt lint test migrate new-migration up down

run-bot:
	python bot/main.py

run-api:
	uvicorn api.main:app --reload --port 8080

fmt:
	python -m pip install ruff black
	ruff check --fix . || true
	black .

lint:
	ruff check .
	black --check .

migrate:
	alembic upgrade head

new-migration:
	alembic revision -m "$(name)" --autogenerate

up:
	docker compose -f docker/docker-compose.yml up -d --build

down:
	docker compose -f docker/docker-compose.yml down -v
