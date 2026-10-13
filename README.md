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

The platform implements Milestone 1 and core Milestone 2 capabilities across API, UI, and Worker:
- **Authentication & RBAC:** Session auth, login rate limiting, project-level role isolation (`owner`, `maintainer`, `viewer`).
- **Environments:** Project-scoped environments with strict SSRF defenses and host allowlists.
- **Test Plans:** Declarative test steps, schema validation, approval workflow, immutable plan versions, and AI-assisted drafting (with strict human approval gates).
- **Adapters:** HTTP API adapter and Playwright Web UI testing adapter.
- **Execution & Isolation:** In-process synchronous execution and durable async RQ background queue execution, runtime resource limits, cancellation, and ephemeral workspace cleanup.
- **Evidence:** Encrypted/redacted response logs, screenshots, and security-header protected downloads.
- **Frontend SPA:** Full React + TypeScript dashboard, environment manager, plan editor, review flow, runs history, and evidence viewer.

## Running with Docker Compose

To start the complete platform stack (PostgreSQL, Redis, API, and Execution Worker):

```bash
docker-compose up --build
```

The API will be available at `http://localhost:8000` and the worker will process queued test runs.

## Security note

- SSRF and private IP restrictions are enforced in application logic and DNS/transport layers (`uitp_policy`).
- For production deployments, enforce network-layer packet egress controls (iptables, cloud security groups, or egress proxies) to provide defense-in-depth against worker breakout.
- Model-generated test plans are strictly declarative drafts that require human review and approval prior to execution.
- See `docs/SECURITY_AND_AI_SAFETY.md` and `docs/WORKER_ISOLATION.md` for details.

