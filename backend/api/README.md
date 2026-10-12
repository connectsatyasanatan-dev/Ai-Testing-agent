# uitp-api

FastAPI control-plane application.

## Running locally

```bash
# From repo root
make dev-api
# or directly:
cd backend/api
uvicorn uitp_api.main:app --reload --host 0.0.0.0 --port 8000
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- OpenAPI schema: http://localhost:8000/openapi.json

## Current state

P0-01/P0-02/P0-03 complete. Only `/health` and `/ready` are live.
Authentication, database, and API routes added in P0-04 → P1.
