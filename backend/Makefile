.PHONY: sync dev test lint fmt typecheck clean

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
