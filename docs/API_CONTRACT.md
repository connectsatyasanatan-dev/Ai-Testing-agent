# Proposed API Contract (MVP)

This is a starting contract, not a statement that endpoints already
exist. Use a versioned prefix such as `/api/v1`. Return consistent error
objects and validate all request bodies.

## Conventions

-   JSON request/response unless downloading an authorized artifact.
-   Authentication required except explicitly public health endpoints.
-   Project-scoped resources require project membership and role checks.
-   Use pagination for collections.
-   Use an idempotency key for run creation.
-   Never return secret values; return configured secret references or
    masked metadata only.
-   All resource primary keys are UUIDv4. Sequential integers must not
    be used for any API-accessible resource.
-   A run cannot be created against a plan version that is not in
    `approved` status. The API returns `422` with error code
    `PLAN_NOT_APPROVED`.
-   Artifact endpoints enforce project-membership authorization on every
    request, independent of identifier unguessability.

## Representative endpoints

### Infrastructure

| Method | Path      | Purpose           |
|--------|-----------|-------------------|
| GET    | `/health` | Process health    |

### Authentication

| Method | Path                     | Purpose              |
|--------|--------------------------|----------------------|
| POST   | `/api/v1/auth/login`     | Start a session      |
| POST   | `/api/v1/auth/logout`    | Revoke session       |
| GET    | `/api/v1/auth/me`        | Current user profile |
| POST   | `/api/v1/auth/tokens`    | Issue API token (for CI/non-browser clients) |
| DELETE | `/api/v1/auth/tokens/{token_id}` | Revoke API token |

### Projects and environments

| Method | Path                                            | Purpose                        |
|--------|-------------------------------------------------|--------------------------------|
| POST   | `/api/v1/projects`                              | Create project                 |
| GET    | `/api/v1/projects`                              | List accessible projects       |
| GET    | `/api/v1/projects/{project_id}`                 | Get project                    |
| PATCH  | `/api/v1/projects/{project_id}`                 | Update project                 |
| POST   | `/api/v1/projects/{project_id}/environments`    | Register environment           |
| GET    | `/api/v1/projects/{project_id}/environments`    | List environments              |
| GET    | `/api/v1/projects/{project_id}/environments/{environment_id}` | Get environment |
| PATCH  | `/api/v1/projects/{project_id}/environments/{environment_id}` | Update environment |

### Plans

| Method | Path                                                   | Purpose                              |
|--------|--------------------------------------------------------|--------------------------------------|
| POST   | `/api/v1/projects/{project_id}/plans`                  | Create plan draft                    |
| GET    | `/api/v1/projects/{project_id}/plans`                  | List plans in project                |
| GET    | `/api/v1/plans/{plan_id}`                              | Get current plan (latest version)    |
| GET    | `/api/v1/plans/{plan_id}/versions`                     | List all versions of a plan          |
| GET    | `/api/v1/plans/{plan_id}/versions/{version}`           | Get a specific plan version          |
| POST   | `/api/v1/plans/{plan_id}/validate`                     | Validate plan (dry-run, no execution)|
| POST   | `/api/v1/plans/{plan_id}/approve`                      | Approve exact plan version           |

### AI-assisted plan generation

| Method | Path                                                      | Purpose                                     |
|--------|-----------------------------------------------------------|---------------------------------------------|
| POST   | `/api/v1/projects/{project_id}/plans/generate`            | Generate a draft plan from intent text      |

> This endpoint is optional at runtime. When no AI provider is
> configured, it returns `503 Service Unavailable` with error code
> `AI_PROVIDER_NOT_CONFIGURED`. The rest of the plan and run API
> remains fully functional. Human-authored plans do not require this
> endpoint.

### Runs

| Method | Path                                       | Purpose                            |
|--------|--------------------------------------------|------------------------------------|
| POST   | `/api/v1/plans/{plan_id}/runs`             | Enqueue run (approved version only)|
| GET    | `/api/v1/runs`                             | List authorized runs               |
| GET    | `/api/v1/runs/{run_id}`                    | Run summary                        |
| GET    | `/api/v1/runs/{run_id}/events`             | Timeline / step events             |
| POST   | `/api/v1/runs/{run_id}/cancel`             | Request cancellation               |

### Artifacts

| Method | Path                                                        | Purpose                                       |
|--------|-------------------------------------------------------------|-----------------------------------------------|
| GET    | `/api/v1/runs/{run_id}/artifacts`                           | List artifacts for a run                      |
| GET    | `/api/v1/runs/{run_id}/artifacts/{artifact_id}`             | Get artifact metadata or download content     |

> Artifact routes are scoped under `runs/` so that run-level
> authorization is enforced before the artifact is resolved. Every
> request additionally verifies that the run's project matches the
> requesting user's membership. UUIDv4 artifact IDs provide
> defence-in-depth but are not a substitute for this authorization
> check.

---

## Example: plan draft

``` json
{
  "name": "Login smoke test",
  "adapter_type": "web",
  "steps": [
    {
      "step_id": "open-login",
      "action": "navigate",
      "inputs": { "url": "https://test.example.com/login" },
      "assertions": [{ "type": "url_contains", "value": "/login" }],
      "timeout_seconds": 15,
      "idempotency_class": "safe",
      "risk_level": "low"
    }
  ]
}
```

The example URL is illustrative. The actual target must be registered
and allowlisted; plan validation must not trust the URL solely because
it appears in a plan.

## Example: AI plan generation request

``` json
POST /api/v1/projects/{project_id}/plans/generate
{
  "intent": "Verify that a registered user can log in and reach the dashboard.",
  "adapter_type": "web",
  "environment_id": "env_01H..."
}
```

Response (draft plan with validation annotations):

``` json
{
  "status": "draft",
  "plan_id": "plan_01H...",
  "version": 1,
  "steps": [
    {
      "step_id": "open-login",
      "action": "navigate",
      "inputs": { "url": "https://test.example.com/login" },
      "assertions": [{ "type": "url_contains", "value": "/login" }],
      "timeout_seconds": 15,
      "idempotency_class": "safe",
      "risk_level": "low",
      "validation_status": "ok"
    }
  ],
  "blocked_steps": [],
  "requires_approval": true,
  "message": "Draft generated. Review, edit if needed, and approve before execution."
}
```

`blocked_steps` lists any step the validator rejected. A plan with
blocked steps cannot be approved until those steps are removed or
corrected. The `requires_approval` field is always `true`; AI-generated
plans cannot bypass the approval gate.

## Example: plan version list

``` json
GET /api/v1/plans/{plan_id}/versions

{
  "plan_id": "plan_01H...",
  "versions": [
    { "version": 3, "status": "approved", "approved_by": "user_01H...", "approved_at": "2026-10-12T09:00:00Z", "created_at": "2026-10-12T08:45:00Z" },
    { "version": 2, "status": "archived", "approved_by": null, "approved_at": null, "created_at": "2026-10-11T14:00:00Z" },
    { "version": 1, "status": "archived", "approved_by": "user_01H...", "approved_at": "2026-10-10T10:00:00Z", "created_at": "2026-10-10T09:30:00Z" }
  ]
}
```

## Example: run response

``` json
{
  "id": "run_01H...",
  "status": "queued",
  "plan_id": "plan_01H...",
  "plan_version": 3,
  "environment_id": "env_01H...",
  "created_at": "2026-10-12T00:00:00Z"
}
```

The `plan_version` field in a run response is always resolvable via
`GET /api/v1/plans/{plan_id}/versions/{version}`.

## Error shape

``` json
{
  "error": {
    "code": "PLAN_NOT_APPROVED",
    "message": "The requested plan version is not approved for execution.",
    "request_id": "req_..."
  }
}
```

## Standard error codes

| Code                        | HTTP status | Meaning                                                        |
|-----------------------------|-------------|----------------------------------------------------------------|
| `PLAN_NOT_APPROVED`         | 422         | Run creation attempted against a non-approved plan version     |
| `POLICY_BLOCKED`            | 422         | A step or run was blocked by project policy                    |
| `UNSUPPORTED_ACTION`        | 422         | Adapter does not support the requested action                  |
| `TARGET_NOT_ALLOWLISTED`    | 422         | Target URL or host is not on the project's allowlist           |
| `AI_PROVIDER_NOT_CONFIGURED`| 503         | AI generation requested but no provider is configured          |
| `AUTHENTICATION_FAILED`     | 401         | Credentials missing or invalid                                 |
| `PERMISSION_DENIED`         | 403         | Authenticated user lacks required role                         |
| `RESOURCE_NOT_FOUND`        | 404         | Resource does not exist or user lacks visibility               |
| `CONFLICT`                  | 409         | Invalid state transition or duplicate non-idempotent operation |
| `VALIDATION_ERROR`          | 422         | Request body fails schema or semantic validation               |

## Contract rules

-   Keep error codes stable and messages safe to show users.
-   Do not expose stack traces or secret values to clients.
-   Use `409 Conflict` for invalid state transitions and duplicate
    non-idempotent operations.
-   Use `422 Unprocessable Entity` for schema-valid JSON that violates
    plan or policy semantics.
-   Use `403 Forbidden` for authenticated users lacking permission;
    avoid leaking the existence of inaccessible resources.
-   Use `404 Not Found` consistently for both missing resources and
    resources the caller is not authorised to know about.
-   Document pagination, filtering, sorting, and rate limits before the
    UI depends on them.
-   The AI generation endpoint (`/plans/generate`) must degrade
    gracefully to `503` when no provider is configured; it must never
    block other endpoints.
