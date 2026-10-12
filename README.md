# Universal AI Testing Platform

Adapter-driven platform for planning, executing, and reporting software tests
with AI assistance — keeping execution deterministic, auditable, and
permission-controlled.

## Repository layout

```
.
├── backend/                Python monorepo
│   ├── api/                FastAPI control-plane (uvicorn entrypoint)
│   ├── packages/
│   │   ├── contracts/      Shared Pydantic schemas — source of truth
│   │   ├── orchestrator/   Run state machine + step dispatch       (P3)
│   │   ├── adapters/
│   │   │   ├── base/       Abstract adapter interface + conformance
│   │   │   ├── http_api/   HTTP API adapter                        (P1)
│   │   │   └── web/        Playwright web adapter                  (P2)
│   │   ├── evidence/       Artifact storage abstraction            (P1)
│   │   └── policy/         RBAC, SSRF checks, redaction            (P0-05)
│   ├── workers/runner/     Worker process entrypoint               (P3)
│   ├── .env.example        Environment variable template (UITP_ prefix)
│   └── README.md
│
├── frontend/               React + TypeScript SPA
│   ├── public/             Static assets
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/         Button, Badge, Card, Spinner, EmptyState, ErrorMessage
│   │   │   └── layout/     AppShell (header + sidebar + main)
│   │   ├── hooks/          Custom React hooks
│   │   ├── lib/            api-client.ts, query-client.ts
│   │   ├── pages/          Dashboard, NotFound  (P4 adds more)
│   │   ├── styles/         Manual CSS (variables → reset → base → index)
│   │   └── types/generated/ AUTO-GENERATED TypeScript types (ADR-010)
│   └── README.md
│
├── tests/                  All Python tests (pytest discovers from here)
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── fixtures/
│       ├── mock_http_api/  Added in P1-00
│       └── web_app/        Added in P2-00
│
├── docs/                   Architecture, API contract, ADRs, PRD …
├── .github/workflows/ci.yml GitHub Actions (lint → type-check → unit → contract → integration)
├── .env.example            Minimal copy — see backend/.env.example for full docs
├── docker-compose.yml      Local infra: PostgreSQL (M1) + Redis (M2)
├── Makefile                Local equivalents of every CI command
└── pyproject.toml          Root ruff / mypy / pytest config
```

## Prerequisites

| Tool | Min version | Notes |
|------|------------|-------|
| Python | 3.12 | `pyenv` or OS package manager |
| Node.js | 20 LTS | `nvm` or OS package manager |
| npm | 10 | Bundled with Node 20 |
| Docker + Compose | 24 | For PostgreSQL; optional for SQLite prototype |

## Quick start

### 1 — Clone and configure

```bash
git clone <repo-url> uitp
cd uitp
cp .env.example .env
# Edit .env — set UITP_SECRET_KEY to a real random value:
#   python -c "import secrets; print(secrets.token_hex(32))"
```

### 2 — Install dependencies

```bash
make install
```

### 3 — Start the database

```bash
make db-start          # starts PostgreSQL container
```

### 4 — Quality checks (no database required)

```bash
make check             # lint + type-check
make test-unit         # unit tests only
```

Expected output:

```
tests/unit/test_health.py::TestHealthEndpoint::test_returns_200      PASSED
tests/unit/test_health.py::TestHealthEndpoint::test_status_is_ok     PASSED
tests/unit/test_health.py::TestHealthEndpoint::test_no_auth_required PASSED
tests/unit/test_contracts.py::TestEnumCompleteness::...              PASSED
...
```

### 5 — Start the API server

```bash
# Run in its own terminal:
make dev-api
```

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}

curl http://localhost:8000/ready
# {"status":"ok","version":"0.1.0","checks":{"database":"not_checked","queue":"not_required"}}
```

### 6 — Start the frontend (optional at Milestone 1)

```bash
# Run in its own terminal:
make dev-web
```

Open http://localhost:5173 — shows the API status dashboard.

## All make targets

```bash
make help              # full target list
make install           # Python + Node deps
make check             # lint + type-check (fast)
make test              # unit + contract
make test-integration  # needs database
make test-e2e          # needs full stack
make generate-types    # regen TS types from API schema
make dev-api           # FastAPI on :8000
make dev-web           # Vite on :5173
make db-start / db-stop
make clean
```

## Current state

**P0-01 ✓ P0-02 ✓ P0-03 ✓** — skeleton, schemas, and config complete.  
`/health` and `/ready` are live. Authentication, database, and adapters are next (P0-04 → P1).

## Security note — local development

Workers run inside the API process in Milestone 1. There is no container
isolation or network egress policy. Use only non-production targets.
Full worker sandboxing is introduced in Milestone 2 (P3-07).
See `docs/SECURITY_AND_AI_SAFETY.md` for the complete threat model.
