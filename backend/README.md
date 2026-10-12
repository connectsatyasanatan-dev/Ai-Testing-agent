# Backend

Python monorepo for the Universal AI Testing Platform.

```
backend/
├── api/                    FastAPI control-plane application
│   └── src/uitp_api/
│       ├── main.py         App factory + CORS + global error handler
│       ├── config.py       Pydantic-Settings (UITP_ prefix, fail-fast)
│       ├── logging_config.py  Text / JSON structured logging
│       └── routers/
│           └── health.py   GET /health  GET /ready
│
├── packages/               Internal Python libraries (never applications)
│   ├── contracts/          Shared Pydantic schemas — single source of truth
│   ├── orchestrator/       Run state machine, step dispatch       (P3)
│   ├── adapters/
│   │   ├── base/           Abstract adapter interface + conformance suite
│   │   ├── http_api/       HTTP API adapter                        (P1)
│   │   └── web/            Playwright browser adapter              (P2)
│   ├── evidence/           Artifact storage abstraction            (P1-06)
│   └── policy/             RBAC, SSRF checks, redaction            (P0-05/P1-02)
│
└── workers/
    └── runner/             Worker entrypoint (async queue in M2)   (P3)
```

## Quick start

```bash
# From repo root — Makefile handles everything
make install-python
make test-unit
make dev-api
```

## Environment variables

Copy `backend/.env.example` to the repo root `.env` and fill in values.
All vars use the `UITP_` prefix (required by pydantic-settings).

| Variable | Required | Description |
|---|---|---|
| `UITP_SECRET_KEY` | Yes | 32+ char random key for session cookies |
| `UITP_DATABASE_URL` | Yes | SQLAlchemy async DSN |
| `UITP_APP_ENV` | No | development (default) / staging / production / test |
| `UITP_REDIS_URL` | M2 only | Redis DSN for RQ worker queue |
| `UITP_AI_API_KEY` | Optional | AI provider key; absent = manual plans only |
