# Mock HTTP API Fixture

**Task:** P1-00  
**Used by:** P1 (HTTP API adapter tests, timeout tests, SSRF policy tests)

This directory provides a deterministic, in-process ASGI application for automated tests and local development. It is **not** a public server or external service.

---

## Intended Test Use & Route Specification

| Route | Method | Status | Response Shape | Intended Test Purpose |
|---|---|---|---|---|
| `/ok` | `GET` | `200 OK` | `{"status": "ok", "message": "...", "data": {"id": "item_123", ...}}` | Validates success path, headers (`Content-Type: application/json`), and JSON-path assertions. |
| `/create` | `POST` | `201 Created` / `400` / `422` | `{"id": "created_456", "status": "created", "received": {...}}` | Tests non-idempotent mutations, valid JSON parsing, 400 on malformed JSON, and 422 on empty body. |
| `/slow` | `GET` | `200 OK` | `{"status": "slow_response", "delayed_seconds": 0.1}` | Exercises client timeouts via `asyncio.sleep(delay)`. Query param `?delay=<sec>` defaults to 0.1s. |
| `/fail` | `GET` | `500 Server Error` | `{"error": {"code": "INTERNAL_SERVER_ERROR", "message": "..."}}` | Exercises error assertions. Returns safe JSON without leaking tracebacks or credentials. |
| `/schema-mismatch` | `GET` | `200 OK` | `{"unexpected_format": true, "items": "...", "count": "..."}` | Valid JSON with intentional type mismatch to verify schema assertion failure reporting. |
| `/redirect` | `GET` | `302 Found` | `Location: /ok` | Tests redirect handling and follow policy. Does not issue outbound network calls. |
| `/private-redirect` | `GET` | `302 Found` | `Location: http://169.254.169.254/latest/meta-data/` | Tests SSRF detection and redirect blocking for private/metadata IPs. Never contacts the target. |

---

## How to Run in Tests

Mount in-process using `httpx.AsyncClient` with `httpx.ASGITransport`:

```python
import httpx
from tests.fixtures.mock_http_api import create_mock_api_app

app = create_mock_api_app()
transport = httpx.ASGITransport(app=app)

async with httpx.AsyncClient(transport=transport, base_url="http://mock-api.local") as client:
    resp = await client.get("/ok")
    assert resp.status_code == 200
```

Each app instance comes with an isolated `app.state.recorder` (`MockApiRecorder`) to inspect calls without relying on global mutable state.
