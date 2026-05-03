.PHONY: sync dev test lint fmt typecheck clean db-up db-down db-shell db-init db-makemigration

sync:
	uv sync --all-groups

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest -v

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests

typecheck:
	uv run mypy src tests

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache

db-up:
	docker compose -f ../docker-compose.yml up -d postgres

db-down:
	docker compose -f ../docker-compose.yml down

db-shell:
	docker exec -it prar-postgres psql -U prar -d prar_agent

db-init:
	uv run alembic upgrade head

db-makemigration:
	uv run alembic revision --autogenerate -m "$(MSG)"
