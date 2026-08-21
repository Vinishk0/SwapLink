.PHONY: install run lint fmt test migrate revision up down logs

install:
	pip install -e ".[dev]"

run:
	python -m app

lint:
	ruff check app tests
	ruff format --check app tests

fmt:
	ruff format app tests
	ruff check --fix app tests

test:
	pytest -q

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f bot
