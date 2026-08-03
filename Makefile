# Knap -- dev & test tasks.
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
lint: ## Run ruff + ty + the line budget
	$(RUFF) check knap_mcp tests scripts
	$(TY) check
	$(PY) scripts/check_max_lines.py

.PHONY: format
format: ## Apply ruff formatting
	$(RUFF) format knap_mcp tests scripts

.PHONY: test
test: ## Unit tests (fake provider, no filesystem)
	$(PYTEST) -m "not integration" -q

.PHONY: test-int
test-int: ## Integration tests against a real seeded vault in a tmpdir
	$(PYTEST) -m integration -q

.PHONY: smoke
smoke: ## Full MCP handshake over stdio against a throwaway vault
	$(PY) scripts/mcp_smoke.py

.PHONY: check
check: ## Open a vault and report what is in it (VAULT=/path/to/vault)
	$(PY) -m knap_mcp --vault "$(VAULT)" --check

.PHONY: docker-build
docker-build: ## Build the container image
	docker build -t knap-mcp:latest .

.PHONY: test-all
test-all: ## Everything CI runs
	$(MAKE) lint
	$(MAKE) format-check
	$(MAKE) test
	$(MAKE) smoke

.PHONY: format-check
format-check: ## Fail if the tree is not formatted
	$(RUFF) format --check knap_mcp tests scripts

.PHONY: clean
clean: ## Remove caches
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
