# Architecture and Technology Choices

## 1. Architecture goals

Keep the core independent of target technology. The core owns plans,
policy, scheduling, state, evidence references, and reporting. Adapters
translate common test actions into target-specific operations. AI is a
planning/analysis component, not the trusted execution authority.

## 2. High-level components

``` text
React UI
   |
FastAPI Control Plane ---- Authentication / RBAC / Projects / Plans / Runs
   |                                  |
   |                         Plan Validator + Policy Engine
   |                                  |
   +-------------------------- Queue / Scheduler
                                      |
                            Isolated Execution Workers
                                      |
                          Adapter Registry / Adapter SDK
                         /              |              \
                  Web Adapter       HTTP API        Future adapters
                   Playwright       HTTPX            Mobile/Desktop/CLI
                                      |
                        Evidence + Structured Events
                                      |
                  PostgreSQL + Object Storage + Observability
                                      |
                         Report / AI Failure Summary
```

## 3. Recommended stack

-   **Frontend:** React + TypeScript, Vite, Shadcn/ui (Radix UI + Tailwind
    CSS) for components, TanStack Query for server state. See ADR-012.
-   **TypeScript types:** Generated from FastAPI OpenAPI schema using
    `openapi-typescript`. Generated output is committed; CI verifies no
    drift. See ADR-010.
-   **Backend API:** Python + FastAPI + Pydantic.
-   **Persistence:** PostgreSQL for shared/team deployments; SQLite may
    be used for early local prototypes only. See ADR-006.
-   **ORM/migrations:** SQLAlchemy + Alembic.
-   **Queue:** RQ (Redis Queue) for Milestone 2 durable orchestration.
    Milestone 1 runs steps synchronously in-process. See ADR-011.
-   **Workers:** Separate processes/containers with per-run limits;
    browser workers include Playwright browsers. See §8 for the
    distinction between local development workers and production workers.
-   **Web automation:** Playwright.
-   **HTTP API testing:** HTTPX plus explicit schema/assertion utilities.
-   **Object storage:** S3-compatible storage for screenshots, traces,
    reports, and larger artifacts. Local filesystem for Milestone 1
    development; swap behind the same interface in Milestone 2.
-   **Authentication:** Session cookies for browser UI (primary); opaque
    API tokens for CI/non-browser clients. See ADR-009.
-   **Password hashing:** Argon2id via a mature library.
-   **Observability:** Structured JSON logs, metrics, traces, health
    checks; OpenTelemetry-compatible instrumentation where useful.
-   **CI:** GitHub Actions. See §9 for job definitions.
-   **Packaging:** Monorepo with workspace-managed frontend packages and
    Python project metadata; Docker Compose for Milestone 2 dependencies
    (PostgreSQL, Redis).
-   **AI provider:** Provider abstraction with structured output,
    timeout, budget, and model configuration. The product must remain
    fully usable with manually authored plans when no model key is
    configured.

Versions should be pinned and checked at implementation time; this
document intentionally avoids claiming a specific latest version.

## 4. Why a monorepo?

A monorepo is a good initial fit because the UI, API contracts, adapter
SDK, docs, and end-to-end tests evolve together. One pull request can
update a schema and all its consumers. Shared CI can test compatibility.
It also makes the product easier for a solo developer to navigate.

### Trade-offs

-   Repository can become large.
-   CI can become slow without path-aware jobs.
-   Python and Node tooling need clear boundaries.
-   Independent releases require discipline.

### Alternatives

-   **Separate repositories:** useful when teams and release cycles are
    independent; adds coordination and contract-version overhead.
-   **Backend-only repo plus separate UI repo:** reasonable if the UI is
    owned by a separate team.
-   **Plugin repositories:** useful later for third-party adapters, but
    too much distribution machinery for the MVP.

### Monorepo rules

-   Define clear package ownership and dependency direction.
-   No frontend-to-database access; all persistence goes through backend
    APIs.
-   Shared contracts are versioned and validated; TypeScript types are
    generated from Python schemas (ADR-010), never written by hand.
-   Do not import internal modules across packages when a public
    interface should exist.
-   CI should run relevant tests by changed path plus a smaller
    integration suite.

## 5. Core data model (initial)

-   `User`: id (UUIDv4), email, password_hash, status, created_at.
-   `Project`: id (UUIDv4), name, owner_id, **status** (`active` |
    `archived`), created_at.
-   `ProjectMembership`: project_id, user_id, role.
-   `Environment`: id (UUIDv4), project_id, name, base_url/target
    descriptor, allowlist, secret_references, **status** (`active` |
    `archived`), created_at.
-   `TestPlan`: id (UUIDv4), project_id, name, version (integer,
    monotonically increasing per plan), source, **status** (`draft` |
    `approved` | `archived`), created_by, approved_by, approved_at,
    created_at.
-   `TestStep`: plan_id, plan_version, order, adapter_type, action,
    input_schema, assertions, timeout_seconds, retry_policy,
    idempotency_class, risk_level.
-   `Run`: id (UUIDv4), plan_id, plan_version, environment_id, status,
    requested_by, worker_id, started_at, finished_at.
-   `StepAttempt`: id (UUIDv4), run_id, step_id, attempt_number, status,
    started_at, finished_at, error_code.
-   `EvidenceArtifact`: id (UUIDv4), run_id, step_attempt_id, type,
    object_key, checksum, redaction_status, retention_until.
-   `AuditEvent`: id (UUIDv4), actor_id, project_id, event_type (see
    §5a), resource_type, resource_id, timestamp, metadata_json,
    metadata_redacted (boolean).
-   `AdapterRegistration`: id (UUIDv4), adapter_type, adapter_version,
    capability_document, status.

All primary keys are UUIDv4. Sequential integer IDs must not be used for
resources that are accessible via API (runs, artifacts, plans) because
sequential IDs enable cross-project enumeration even when authorization
checks are in place.

Projects, environments, and plans are **soft-deleted only** (status set
to `archived`). Hard deletes that would orphan run history are not
permitted.

### 5a. Audit event taxonomy (minimum required)

The following event types must be recorded. Additional types may be
added but these cannot be omitted from migrations or contract tests.

| Event type                     | Triggered by                                   |
|-------------------------------|------------------------------------------------|
| `user_login`                  | Successful authentication                      |
| `user_login_failed`           | Failed authentication attempt                  |
| `role_change`                 | Project membership role updated                |
| `project_created`             | New project created                            |
| `project_archived`            | Project status set to archived                 |
| `environment_created`         | New environment registered                     |
| `environment_modified`        | Environment configuration changed              |
| `secret_reference_updated`    | Secret reference added, rotated, or removed    |
| `plan_created`                | New plan draft created                         |
| `plan_approved`               | Plan version approved for execution            |
| `plan_archived`               | Plan version archived                          |
| `run_created`                 | Run enqueued                                   |
| `run_cancelled`               | Run cancelled by user or policy                |
| `policy_blocked`              | A step or run was blocked by policy            |
| `artifact_accessed`           | Artifact metadata or content retrieved         |

The `metadata_json` field stores event-specific context (e.g., which
role was assigned, which plan version was approved). `metadata_redacted`
is set to `true` when the metadata was sanitised before storage.
Secrets, credential values, and authorization header content must never
appear in `metadata_json`.

### 5b. Idempotency and retry classification

Every `TestStep` carries an `idempotency_class` field. The step schema
and the adapter must agree on this value. It is not inferred solely from
the HTTP method.

| Class          | Meaning                                                        | Default retry behaviour               |
|----------------|----------------------------------------------------------------|---------------------------------------|
| `safe`         | Read-only; repeating produces no side effects (e.g., GET, HEAD, browser navigate to a page). | Retry up to the configured limit.    |
| `idempotent`   | May mutate state but produces the same result on repetition (e.g., PUT with a known ID, a browser assertion). | Retry up to the configured limit.    |
| `non_idempotent` | Mutation whose repetition would create a duplicate or unwanted side effect (e.g., POST to create a resource, form submit, payment). | **Do not retry by default.** Retry is permitted only when `retry_policy.allow_non_idempotent: true` is explicitly set on the step and the adapter confirms the action is safe to repeat in context. |
| `unknown`      | Classification not determinable at plan time.                 | Treat as `non_idempotent`.            |

HTTP method is a **hint** only, not the authoritative classification.
A POST used as a health check may be `safe`; a GET that triggers a
side-effectful webhook integration may be `non_idempotent`. The plan
author or AI generator must set `idempotency_class` explicitly.

Acceptance test requirement (P1-04): a step with `action: POST` and
`idempotency_class: non_idempotent` and a retry policy must not be
retried automatically; the test must verify the step is attempted
exactly once and the retry policy is not applied.

## 6. Execution lifecycle

1.  `draft` plan is authored or AI-proposed.
2.  Validator checks schema, capabilities, policy, target, risk, and
    budgets.
3.  Human approval marks the exact plan version `approved`.
4.  Run request is authorized. The API **must** verify the plan version
    is in `approved` status before enqueueing. A run against a `draft`,
    `archived`, or non-existent plan version is rejected with
    `PLAN_NOT_APPROVED` (HTTP 422).
5.  Run is enqueued (Milestone 2) or executed synchronously
    (Milestone 1 development mode — see §8).
6.  Worker claims run using a lease/heartbeat (Milestone 2 only).
7.  Each step creates an attempt and emits events.
8.  Adapter actions run under timeout and resource constraints.
9.  Assertions and policy determine the step status.
10. Artifacts are redacted, stored, and linked.
11. Orchestrator computes final status and closes the run.
12. Report summarises outcomes without replacing raw evidence.

## 7. Reliability considerations

-   Use durable queue state and worker heartbeats (Milestone 2).
-   Use idempotency keys for run-creation requests.
-   Distinguish retryable from non-retryable errors using the
    `idempotency_class` on the step (see §5b).
-   Do not blindly retry non-idempotent actions (POST creates, deletes,
    form submissions, payments).
-   Persist state transitions; on recovery, reconcile worker leases
    before resuming (Milestone 2).
-   Cancellation must propagate to adapter operations and child
    processes.
-   Apply per-project concurrency limits and global worker quotas.

## 8. Deployment shape and worker security model

### Milestone 1 — local development (synchronous, no queue)

-   UI dev server.
-   FastAPI API process (steps executed synchronously in-process via a
    thread executor or direct adapter call).
-   PostgreSQL database (Docker container or local install).
-   Local artifact directory (filesystem, not object storage).
-   Optional AI provider key.

> **Security note — Milestone 1 workers are NOT sandboxed.**
> Steps run inside the API process or a plain Python subprocess. There
> is no container-level network isolation, no capability dropping, no
> read-only filesystem enforcement, and no egress policy beyond
> application-layer SSRF checks (P1-02). This mode is acceptable for
> local development against non-production targets. It must not be used
> against production systems or shared environments. The worker security
> properties described in Milestone 2 below are **not present** in
> Milestone 1.

### Milestone 2 — full MVP (async queue, isolated workers)

-   HTTPS ingress.
-   FastAPI API replicas.
-   PostgreSQL (managed or containerised).
-   Redis + RQ worker pool (see ADR-011).
-   **Isolated worker containers:** non-root user, read-only base
    filesystem where practical, CPU/memory/PID limits, per-run temporary
    directories.
-   **Network egress policy** enforced at the container/network layer
    in addition to application-layer SSRF checks. This is a hard
    requirement before workers execute tests against any shared or
    production environment.
-   S3-compatible object storage for artifacts.
-   Secret manager for credential injection.
-   Centralised monitoring and alerting.
-   Backups and tested restore procedure.

### Production

-   All Milestone 2 controls required.
-   Separate dev, staging, and production credential sets.
-   Worker pool isolated from control plane network.
-   Object storage with lifecycle policy aligned to retention settings.

## 9. CI baseline (GitHub Actions)

The default CI system is **GitHub Actions**, assuming the repository is
hosted on GitHub. All commands must also be runnable locally without CI.

Minimum job set:

| Job              | Trigger            | Commands (representative)                                   |
|------------------|--------------------|--------------------------------------------------------------|
| `lint`           | Every push/PR      | `ruff check .` (Python), `eslint src/` (TypeScript)         |
| `type-check`     | Every push/PR      | `mypy packages/ apps/api/` (Python), `tsc --noEmit` (TS)   |
| `unit`           | Every push/PR      | `pytest tests/unit/`                                         |
| `contract`       | Every push/PR      | `pytest tests/contract/`                                     |
| `integration`    | Every push/PR      | `pytest tests/integration/` (requires DB; use service containers) |
| `generate-types` | Every push/PR      | Regenerate TS types and fail if committed output differs     |
| `e2e`            | Main branch / release | `pytest tests/e2e/` (requires full stack)                |
| `security`       | Main branch / release | Dependency vulnerability scan (`pip-audit`, `npm audit`)  |

Path-aware filtering: lint/type-check/unit/contract jobs run only when
files in their relevant package have changed, plus always on the main
branch. Integration and e2e jobs always run on main branch and on PRs
that touch `packages/`, `apps/`, or `workers/`.

Local equivalents: document all commands in a `Makefile` or `justfile`
at the repo root so developers can run any CI step without GitHub.

## 10. Important boundary

The AI model may return a structured plan proposal or a failure
explanation. It must not directly receive unrestricted shell access,
database credentials, cloud credentials, or arbitrary network access.
The validator and worker enforce all allowed operations independently of
model output. Manual plan authoring must work at all times regardless of
whether an AI provider key is configured.
