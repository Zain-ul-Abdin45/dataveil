.DEFAULT_GOAL := help
.PHONY: help setup format lint typecheck test check

PY ?= .venv/bin/python

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Create .venv and install dataveil with all dev extras
	python3 -m venv .venv
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m spacy download en_core_web_sm
	$(PY) -m pre_commit install

format: ## Format the code with ruff
	$(PY) -m ruff format dataveil tests examples
	$(PY) -m ruff check --fix dataveil tests examples

lint: ## Check lint and formatting (read-only)
	$(PY) -m ruff check dataveil tests examples
	$(PY) -m ruff format --check dataveil tests examples

typecheck: ## Type-check the package with mypy
	$(PY) -m mypy dataveil

test: ## Run the test suite
	$(PY) -m pytest

check: lint typecheck test ## Run every check CI runs
