.PHONY: install test lint fmt run

install:
	pip install -e ".[dev]"

test:
	pytest -x

lint:
	ruff check src tests
	mypy src

fmt:
	ruff format src tests
	ruff check --fix src tests

run:
	uvicorn obsidian_writer.app:app --host 0.0.0.0 --port 4040 --reload
