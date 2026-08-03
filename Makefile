# Obsidian Pro -- dev & test tasks.
.DEFAULT_GOAL := help
VENV := .venv
PY := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
TY := $(VENV)/bin/ty

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

.PHONY: install
install: ## Create venv and install the package with dev extras
	uv venv --python 3.10
	uv pip install -e ".[dev]"

.PHONY: lint
lint: ## Run ruff + ty
	$(RUFF) check obsidian_mcp tests
	$(TY) check

.PHONY: format
format: ## Apply ruff formatting
	$(RUFF) format obsidian_mcp tests

.PHONY: test
test: ## Unit tests (fake provider, no filesystem)
	$(PYTEST) -m "not integration" -q

.PHONY: test-int
test-int: ## Integration tests against a real seeded vault in a tmpdir
	$(PYTEST) -m integration -q

.PHONY: test-all
test-all: ## Everything CI runs
	$(MAKE) lint
	$(MAKE) test
	$(MAKE) test-int

.PHONY: clean
clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
