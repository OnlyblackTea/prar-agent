.PHONY: sync dev test lint fmt typecheck clean db-up db-down db-shell db-init db-makemigration coverage-state-machine smoke-llm

sync:
	uv sync --all-groups

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest -v -m "not smoke"

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

coverage-state-machine:
	uv run pytest --cov=app.core.state_machine --cov-branch --cov-report=term-missing --cov-fail-under=100 tests/test_state_machine.py

smoke-llm:
	@if [ -z "$$ANTHROPIC_API_KEY$$OPENAI_API_KEY" ]; then \
		echo "ERROR: 至少设一个 ANTHROPIC_API_KEY 或 OPENAI_API_KEY"; exit 1; \
	fi
	uv run pytest -v -m smoke tests/test_router.py
