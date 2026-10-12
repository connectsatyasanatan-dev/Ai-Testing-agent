# Implementation Backlog

**Status values:** `todo`, `in_progress`, `blocked`, `done`. Change
status only when verified in the repository.

**Milestone 1 — HTTP API slice:** P0 + P1 complete. Demonstrable
without a web browser, a queue, or an AI provider. See
ROADMAP_AND_TEST_STRATEGY.md for exit criteria.

**Milestone 2 — Full MVP:** P0 through P5 core complete. Includes the
Playwright web adapter, async worker queue, React UI, and optional AI
plan generation. Satisfies PRD §12 acceptance criteria.

Do not claim Milestone 2 (Full MVP) is complete after only Milestone 1.

---

## P0 --- foundation

-   [x] **P0-00** Resolve and record all open decisions before any
    implementation begins.
    -   Acceptance: DECISIONS.md shows `Accepted` status for ADR-009
        (auth), ADR-010 (type generation), ADR-011 (queue), ADR-012 (UI
        library), and both remaining open decisions (AI provider, artifact
        retention) are at minimum assigned an owner and a deadline.
    -   Note: ADR-009 through ADR-012 are already accepted in
        DECISIONS.md. This task confirms they are read and understood
        before P0-01 begins, and captures the two remaining open items.

-   [x] **P0-01** Create monorepo skeleton and documented local setup.
    -   Acceptance: fresh checkout can install dependencies and run API/UI
        checks using documented commands.
    -   CI system: **GitHub Actions**. Minimum jobs: `lint`, `type-check`,
        `unit`, `contract`, `generate-types`. See ARCHITECTURE.md §9 for
        job definitions.
    -   A `Makefile` (or `justfile`) at the repo root provides local
        equivalents of every CI command.
    -   `docker-compose.yml` is present for Milestone 2 dependencies
        (PostgreSQL, Redis) but is **not required** to run Milestone 1.
    -   Repository structure follows the layout in README.md with
        `tests/fixtures/mock_http_api/` and `tests/fixtures/web_app/`
        directories created as placeholders.

-   [ ] **P0-02** Add shared schemas for plan, step, run status, adapter
    capabilities, and evidence metadata.
    -   Acceptance: schema validation tests cover valid and invalid
        examples.
    -   The `packages/contracts/` Python package is the source of truth.
    -   `make generate-types` produces TypeScript output in
        `frontend/src/types/generated/`; CI fails if committed output differs
        from freshly generated output.
    -   `idempotency_class` field (`safe` | `idempotent` |
        `non_idempotent` | `unknown`) is present on the step schema.
    -   Status: Incomplete (reopened). Pydantic contracts and validations
        in `uitp_contracts` are complete, but TypeScript types in
        `frontend/src/types/generated/api.d.ts` are a placeholder and the CI
        drift check (`generate-types-check`) is disabled.

-   [x] **P0-03** Add configuration management and safe example
    environment file.
    -   Acceptance: missing required config fails clearly; secrets are
        not logged.

-   [x] **P0-04** Add database migrations and core models.
    -   Acceptance: migrations build a fresh database and upgrade test
        succeeds.
    -   All primary keys are UUIDv4.
    -   `Project`, `Environment`, and `TestPlan` have a `status` field
        supporting soft-delete (`active`/`archived`). Hard deletes that
        would orphan run history are not permitted.
    -   `AuditEvent` migration includes all event types from
        ARCHITECTURE.md §5a.
    -   Default artifact retention period is resolved before this task
        is marked `done`; value is recorded in DECISIONS.md.

-   [x] **P0-05** Add authentication, project membership, and RBAC.
    -   Acceptance: cross-project access tests return denial.
    -   Implement session-cookie authentication (ADR-009).
    -   Rate-limit the login endpoint.
    -   Audit `user_login` and `user_login_failed` events.
    -   API token issuance (`POST /api/v1/auth/tokens`) is deferred to
        Milestone 2 (before P4-01). Note the deferral in code comments.

-   [x] **P1-ENV** Implement Environment Management as a Complete Vertical Slice.
    -   Acceptance: project-scoped environment lifecycle endpoints:
        `POST /api/v1/projects/{project_id}/environments`,
        `GET /api/v1/projects/{project_id}/environments` (with `include_archived` filter),
        `GET /api/v1/projects/{project_id}/environments/{environment_id}`,
        `PATCH /api/v1/projects/{project_id}/environments/{environment_id}`,
        `DELETE /api/v1/projects/{project_id}/environments/{environment_id}` (204 No Content).
    -   SSRF policy & target validation integrated: blocks prohibited cloud metadata endpoints (169.254.169.254, metadata.google.internal) across URLs and allowlist items.
    -   Cross-project access returns 404 (masking project existence).
    -   Insufficient project roles return 403 (viewers cannot create/update/delete).
    -   Soft-delete conventions applied (`status='archived'`) and excluded from normal lists.
    -   Safe audit logging records `environment_created`, `environment_modified`, and `secret_reference_updated` without leaking raw credentials or secret values.
    -   Full React + TypeScript frontend management page (`/environments`, `/projects/:projectId/environments`):
        card list, loading, empty, and error states, create/edit modals, allowlist management, masked secret reference indicators, and delete/archive confirmation.
    -   Verified with 26 unit and integration tests, 0 ESLint errors, and clean TypeScript compilation.

---

## P1 --- deterministic HTTP API adapter

-   [x] **P1-00** Create mock HTTP API fixture application.
    -   Acceptance: a minimal ASGI server starts in-process for tests
        and exposes the following routes:
        -   `GET /ok` → 200 with JSON body.
        -   `POST /create` → 201 with JSON body (non-idempotent).
        -   `GET /slow` → 200 after a configurable delay (for timeout
            tests).
        -   `GET /fail` → 500 with error body.
        -   `GET /schema-mismatch` → 200 with a body that fails JSON
            schema assertions.
        -   `GET /redirect` → 302 redirect (for SSRF redirect tests).
        -   `GET /private-redirect` → 302 redirect to a private IP
            (for SSRF block tests).
    -   Fixture lives in `tests/fixtures/mock_http_api/`.

-   [x] **P1-01** Implement adapter registry and capability negotiation.
    -   Acceptance: unknown adapter/action is blocked with typed error.

-   [x] **P1-02** Implement target allowlist and SSRF-resistant URL
    validation.
    -   Acceptance: tests cover redirects, private/loopback/link-local
        IPs, and cloud metadata addresses (169.254.169.254, etc.).
    -   Note: this is the application-layer SSRF control only.
        Network-layer egress enforcement is a Milestone 2 (Phase 4)
        requirement. Document this gap in comments.

-   [x] **P1-03** Implement declarative API step schema.
    -   Acceptance: method, URL, headers, body, timeout, assertions,
        `idempotency_class`, and `retry_policy` validate against schema.
    -   Immutable plan versioning, concurrency-safe TOCTOU approval gate,
        and audit event logging implemented and verified.

-   [x] **P1-04** Implement HTTP adapter using bounded HTTP requests.
    -   Acceptance:
        -   Mock API success/failure/timeout paths all work.
        -   A step with `action: POST`, `idempotency_class: non_idempotent`,
            and a `retry_policy` is attempted **exactly once**; the retry
            policy is not applied and a warning is logged.
        -   A step with `action: GET`, `idempotency_class: safe`, and a
            `retry_policy` retries up to the configured limit on failure.
        -   HTTP method alone does not determine retry eligibility;
            `idempotency_class` is the authoritative field.

-   [x] **P1-05** Implement assertion engine for status, JSON path,
    headers, and schema.
    -   Acceptance: failed assertions are reported as `Failed`, not
        exceptions hidden as a pass.

-   [x] **P1-06** Implement structured run/step event and evidence
    metadata.
    -   *P1-06A completed:* Synchronous execution pipeline, exact-version
        approval gate (422 `PLAN_NOT_APPROVED`), StepAttempt recording,
        and timeline events implemented.
    -   *P1-06B completed:* Evidence storage abstraction
        (`LocalFilesystemEvidenceStorage`) with atomic write strategy,
        SHA-256 integrity verification, path traversal/symlink escape defenses,
        bounded 10MB payload size limits, sensitive-data redaction engine
        (`RedactionEngine`), 30-day default retention (ADR-013),
        project-authorized retrieval endpoints (`GET /api/v1/runs/{run_id}/artifacts`
        and `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`), tenant isolation
        404 masking, and `ARTIFACT_ACCESSED` audit trail integration.
    -   Acceptance: each attempt has timestamps, status, error code, and
        evidence references.
    -   Evidence files are stored in the configured local directory
        (Milestone 1 mode).
    -   Acceptance test: a run cannot be created against a draft,
        unapproved, or archived plan version; API returns `422` with
        error code `PLAN_NOT_APPROVED`.

---

## P2 --- web adapter (Playwright)

-   [ ] **P2-00** Create simple web test application fixture.
    -   Acceptance: a minimal static web application serves locally with:
        -   A login form with username and password fields.
        -   A post-login dashboard page.
        -   A navigation element with at least two links.
        -   An element with a known text value for assertion tests.
        -   An element that is conditionally visible (for
            visibility-assertion tests).
    -   Fixture lives in `tests/fixtures/web_app/`.

-   [ ] **P2-01** Implement Playwright worker and lifecycle.
    -   Note: this phase runs synchronously in-process (Milestone 1
        mode). There is no queue, no lease, no heartbeat, and no durable
        recovery. These are explicit development shortcuts; add a
        `# MILESTONE-1-SYNC-MODE` comment wherever this shortcut is
        applied. Do not claim production worker guarantees in this mode.

-   [ ] **P2-02** Add declarative browser actions: navigate, click,
    fill, select, wait.

-   [ ] **P2-03** Add locator/text/URL/visibility assertions.

-   [ ] **P2-04** Capture screenshots and traces with redaction controls.

-   [ ] **P2-05** Add cancellation, timeout, and cleanup tests.
    -   Acceptance: demo web flow passes; intentional assertion failure
        has useful evidence; browser context is cleaned up after every
        run regardless of outcome.
    -   Note: cancellation in synchronous mode is best-effort only.
        Durable cancellation propagation requires P3.

---

## P3 --- durable orchestration

-   [x] **P3-01** Add RQ queue and separate worker process.
    -   Note: this task promotes the existing synchronous adapter calls
        (Milestone 1 mode) into async RQ jobs. The step/run event schema
        must remain unchanged. Remove all `# MILESTONE-1-SYNC-MODE`
        comments as each path is migrated.
    -   Acceptance: a run that was previously synchronous now executes
        via the RQ queue; step events and evidence format are identical
        to the synchronous implementation.

-   [ ] **P3-02** Add run state machine, worker leases, heartbeat, and
    recovery.
    -   Acceptance: a worker killed mid-run does not cause the run to be
        silently marked `passed`; lease expiry triggers recovery.

-   [ ] **P3-03** Add idempotency key for run creation.
    -   Acceptance: duplicate run-creation requests with the same key
        return the existing run rather than creating a second one.

-   [ ] **P3-04** Add cancellation and bounded retry policy.
    -   Acceptance: cancellation propagates to the adapter and any child
        process; retry policy respects `idempotency_class`.

-   [ ] **P3-05** Add concurrency limits and per-project quotas.

-   [ ] **P3-06** Verify approval gate at run creation (TOCTOU).
    -   Acceptance: a run cannot be created against a plan version that
        is not in `approved` status, even under concurrent requests. The
        check is atomic. Tested cases:
        -   Plan version in `draft` → `422 PLAN_NOT_APPROVED`.
        -   Plan version in `archived` → `422 PLAN_NOT_APPROVED`.
        -   Plan version approved then immediately archived before run
            creation completes → `422 PLAN_NOT_APPROVED`.

-   [ ] **P3-07** Implement and verify container worker isolation and
    network egress policy.
    -   Acceptance: worker container runs as non-root; network egress to
        private IPs and metadata addresses is blocked at the network
        layer; test confirms a blocked egress attempt from within the
        container is denied independently of application code.
    -   Note: this is required before Milestone 2 is declared complete.

---

## P4 --- user interface

-   [ ] **P4-00** Implement API token issuance and revocation.
    -   Acceptance: `POST /api/v1/auth/tokens` issues a short-lived
        opaque token; `DELETE /api/v1/auth/tokens/{token_id}` revokes
        it; CI scripts can authenticate without a browser session.
    -   Note: deferred from P0-05 per ADR-009.

-   [ ] **P4-01** Authentication and project navigation (UI).
    -   Requires: CSS Modules design tokens and layout components configured (ADR-012).

-   [ ] **P4-02** Project and environment management (UI).

-   [ ] **P4-03** Plan editor with schema validation feedback (UI).

-   [ ] **P4-04** Plan review/approval and version history (UI).
    -   Requires: `GET /api/v1/plans/{plan_id}/versions` and
        `GET /api/v1/plans/{plan_id}/versions/{version}` endpoints.

-   [ ] **P4-05** Run list, run timeline, step details, artifacts, and
    filters (UI).

-   [ ] **P4-06** Loading, empty, error, blocked, and inconclusive
    states (UI).

---

## P5 --- AI assistance

-   [ ] **P5-00** Design and stub AI plan generation endpoint.
    -   Acceptance: `POST /api/v1/projects/{project_id}/plans/generate`
        is implemented and returns `503` with error code
        `AI_PROVIDER_NOT_CONFIGURED` when no provider key is set.
        Schema for request (intent text, adapter type, environment ID)
        and response (draft plan + blocked steps + `requires_approval:
        true`) is defined in the contracts package and matches
        API_CONTRACT.md.

-   [ ] **P5-01** Provider interface with timeouts, budgets, and
    structured output.

-   [ ] **P5-02** Generate draft plans from acceptance criteria.
    -   Acceptance: generated plan contains `idempotency_class` on every
        step; no step contains arbitrary executable code.

-   [ ] **P5-03** Validate and explain rejected steps.

-   [ ] **P5-04** Require approval before execution.
    -   Acceptance: AI-generated plans have `requires_approval: true`
        and cannot execute without the approval gate (same gate as
        manual plans).

-   [ ] **P5-05** Generate evidence-grounded failure summaries.
    -   Acceptance: manual plans still work without a provider key;
        prompt injection fixtures do not change policy or execution
        outcome.

---

## P6 --- security and release hardening

-   [ ] **P6-01** Central redaction and artifact access controls.
    -   Acceptance: all artifact retrieval enforces project-membership
        authorization; `artifact_accessed` audit events are recorded.

-   [ ] **P6-02** Audit logs for approvals, environment changes, and
    runs.
    -   Acceptance: all event types in ARCHITECTURE.md §5a are present
        in the database and covered by tests.

-   [ ] **P6-03** Dependency and security scanning in CI.
    -   Acceptance: `pip-audit` and `npm audit` run on every main-branch
        build; a critical-severity finding blocks the build.

-   [ ] **P6-04** Observability, alerts, backup and restore drill.

-   [ ] **P6-05** Deployment guide and operational runbook.
    -   Must include: explicit statement of which security controls
        are local-dev only vs. required for staging/production; worker
        container setup; network egress policy configuration.

-   [ ] **P6-06** End-to-end, security, resilience, and performance test
    suites.

---

## Later adapter candidates

These are extensibility goals. None are MVP features. Do not implement
or claim support for any of these in Milestone 1 or Milestone 2.

-   [ ] **L-01** Mobile adapter with device/emulator isolation.
-   [ ] **L-02** Desktop adapter with OS-specific runner.
-   [ ] **L-03** Restricted CLI adapter using command allowlists.
-   [ ] **L-04** Event/message adapter with isolated test topics.
-   [ ] **L-05** Read-only database validation adapter.
-   [ ] **L-06** Visual regression and accessibility capabilities.

---

## Milestone 1 recommended task order

Complete in sequence. Do not start P1 until P0 is complete.

1.  P0-00 → P0-01 → P0-02 → P0-03 → P0-04 → P0-05
2.  P1-00 → P1-01 → P1-02 → P1-03 → P1-04 → P1-05 → P1-06

At the end of this sequence, Milestone 1 exit criteria must be verified
before beginning Milestone 2 work (P2 onward).
