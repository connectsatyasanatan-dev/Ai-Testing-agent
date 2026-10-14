# =============================================================================
# Makefile — Universal AI Testing Platform
#
# Two sub-projects:
#   backend/   Python (FastAPI + packages + workers)
#   frontend/  React + TypeScript
#
# Tests live at repo root: tests/
#
# Quick start:
#   make install     Install all deps (Python + Node)
#   make check       lint + type-check (fast, no I/O)
#   make test        unit + contract tests
#   make dev-api     Start FastAPI dev server  (separate terminal)
#   make dev-web     Start Vite dev server     (separate terminal)
# =============================================================================

.DEFAULT_GOAL := help

.PHONY: help install install-python install-node \
        lint lint-python lint-node \
        type-check type-check-python type-check-node \
        check \
        test test-unit test-contract test-integration test-e2e test-security \
        generate-types check-types-drift \
        dev-api dev-web \
        db-start db-stop \
        clean

# ── Paths ─────────────────────────────────────────────────────────────────────
PYTHON   := python
PIP      := $(PYTHON) -m pip
PYTEST   := $(PYTHON) -m pytest
MYPY     := $(PYTHON) -m mypy
RUFF     := $(PYTHON) -m ruff
API_DIR  := backend/api
WEB_DIR  := frontend
PKG_DIR  := backend/packages
WRK_DIR  := backend/workers
TEST_DIR := tests

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "Universal AI Testing Platform"
	@echo ""
	@echo "  install              Install Python and Node dependencies"
	@echo "  install-python       Python deps only"
	@echo "  install-node         Node deps only"
	@echo ""
	@echo "  check                lint + type-check  (fast, no I/O)"
	@echo "  lint                 ruff + eslint"
	@echo "  type-check           mypy + tsc"
	@echo ""
	@echo "  test                 unit + contract tests"
	@echo "  test-unit            pytest tests/unit/"
	@echo "  test-contract        pytest tests/contract/"
	@echo "  test-integration     pytest tests/integration/  (needs DB)"
	@echo "  test-e2e             pytest tests/e2e/          (needs full stack)"
	@echo "  test-security        pytest tests/security/"
	@echo ""
	@echo "  generate-types       Regen TS types from OpenAPI  (API must be running)"
	@echo "  check-types-drift    Fail if committed types differ"
	@echo ""
	@echo "  dev-api              FastAPI on http://localhost:8090  (run in own terminal)"
	@echo "  dev-web              Vite on   http://localhost:5180   (run in own terminal)"
	@echo ""
	@echo "  db-start             docker compose up postgres -d"
	@echo "  db-stop              docker compose down"
	@echo "  clean                Remove caches and build output"
	@echo ""

# ── Install ───────────────────────────────────────────────────────────────────
install: install-python install-node

install-python:
	@echo "==> Installing Python packages (editable)"
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(PKG_DIR)/contracts[dev]"
	$(PIP) install -e "$(PKG_DIR)/orchestrator[dev]"
	$(PIP) install -e "$(PKG_DIR)/adapters/base[dev]"
	$(PIP) install -e "$(PKG_DIR)/adapters/http_api[dev]"
	$(PIP) install -e "$(PKG_DIR)/adapters/web[dev]"
	$(PIP) install -e "$(PKG_DIR)/evidence[dev]"
	$(PIP) install -e "$(PKG_DIR)/policy[dev]"
	$(PIP) install -e "$(API_DIR)[dev]"
	$(PIP) install -e "$(WRK_DIR)/runner[dev]"
	$(PIP) install "pytest-asyncio>=0.23,<1" "anyio[trio]>=4.4,<5" "httpx>=0.27,<1"

install-node:
	@echo "==> Installing Node packages"
	cd $(WEB_DIR) && npm install

# ── Lint ──────────────────────────────────────────────────────────────────────
lint: lint-python lint-node

lint-python:
	@echo "==> Python lint (ruff)"
	$(RUFF) check $(PKG_DIR)/ $(API_DIR)/ $(WRK_DIR)/ $(TEST_DIR)/

lint-node:
	@echo "==> TypeScript lint (eslint)"
	cd $(WEB_DIR) && npm run lint

# ── Type-check ────────────────────────────────────────────────────────────────
type-check: type-check-python type-check-node

type-check-python:
	@echo "==> Python type-check (mypy)"
	$(MYPY) $(PKG_DIR)/ $(API_DIR)/ $(WRK_DIR)/

type-check-node:
	@echo "==> TypeScript type-check (tsc)"
	cd $(WEB_DIR) && npm run type-check

check: lint type-check

# ── Tests ─────────────────────────────────────────────────────────────────────
test: test-unit test-contract

test-unit:
	@echo "==> Unit tests"
	$(PYTEST) $(TEST_DIR)/unit/ -v

test-contract:
	@echo "==> Contract tests"
	$(PYTEST) $(TEST_DIR)/contract/ -v

test-integration:
	@echo "==> Integration tests  (requires database)"
	$(PYTEST) $(TEST_DIR)/integration/ -v

test-e2e:
	@echo "==> End-to-end tests  (requires full stack)"
	$(PYTEST) $(TEST_DIR)/e2e/ -v

test-security:
	@echo "==> Security tests"
	$(PYTEST) $(TEST_DIR)/security/ -v

# ── TypeScript type generation (ADR-010) ─────────────────────────────────────
generate-types:
	@echo "==> Generating TypeScript types  (API must be running on :8090)"
	cd $(WEB_DIR) && npm run generate-types

check-types-drift:
	@echo "==> Checking TypeScript type drift"
	cd $(WEB_DIR) && npm run check-types-drift

# ── Dev servers  (run in separate terminals) ──────────────────────────────────
dev-api:
	@echo "==> FastAPI dev server  → http://localhost:8090"
	cd $(API_DIR) && uvicorn uitp_api.main:app --reload --host 0.0.0.0 --port 8090

dev-web:
	@echo "==> Vite dev server  → http://localhost:5180"
	cd $(WEB_DIR) && npm run dev

# ── Infrastructure ────────────────────────────────────────────────────────────
db-start:
	docker compose up postgres -d

db-stop:
	docker compose down

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__   -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info"  -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache"   -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache"   -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(WEB_DIR)/dist $(WEB_DIR)/.vite $(WEB_DIR)/coverage
	@echo "Clean done."
