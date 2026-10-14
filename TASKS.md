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

-   [x] **P0-02** Add shared schemas for plan, step, run status, adapter
    capabilities, and evidence metadata.
    -   *Completed:* `uitp_contracts` schemas complete with explicit `idempotency_class`,
        `RiskLevel`, and `AuditEventType`. `npm run generate-types` generates full TypeScript
        definitions into `frontend/src/types/generated/api.d.ts` using `openapi-typescript 7.13.0`.
        `npm run check-types-drift` validates in CI that committed types match OpenAPI without drift.

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

-   [x] **P2-00** Create simple web test application fixture.
    -   *Completed:* Created Starlette-based local test fixture in `tests/fixtures/web_app/app.py`
        serving `/login`, `/dashboard`, `/settings`, `/slow`, `/redirect`, `/private-redirect`
        (302 to `169.254.169.254`), and `/popup`. Includes deterministic assertions,
        conditional visibility toggle, and `run_test_web_server` context manager.
    -   Fixture lives in `tests/fixtures/web_app/`.

-   [x] **P2-01** Implement Playwright worker and lifecycle.
    -   *Completed:* Implemented `PlaywrightAdapter(BaseAdapter)` in `backend/packages/adapters/web/`
        registered in adapter registry (`AdapterType.WEB`). Supports headless Chromium, isolated
        browser contexts, multi-step session reuse (`get_or_create_session`), bounded timeouts,
        clean teardown (`close_session`) in worker/API `finally` blocks, and worker health check probing.

-   [x] **P2-02** Add declarative browser actions: navigate, click,
    fill, select, wait.
    -   *Completed:* Implemented safe declarative action dispatching with `WebLocatorParser`
        (supporting `css`, `text`, `test_id`, `role`, `label`, `placeholder` while strictly
        rejecting `eval`, `javascript:`, and arbitrary code). Network-level SSRF defense
        (`BrowserSecurityRouter`) attaches `context.route("**/*")` to validate all destinations,
        intercept and validate 3xx redirects (`max_redirects=0`), abort blocked requests
        (`blockedbyclient`), limit popups (`max_pages=3`), and auto-cancel downloads.

-   [x] **P2-03** Add locator/text/URL/visibility assertions.
    -   *Completed:* Implemented `WebAssertionEngine` evaluating `url_contains`, `url_equals`,
        `title_contains`, `title_equals`, `text_visible`, `element_visible`, and `element_hidden`.
        Failed assertions cleanly produce `StepStatus.FAILED` with deterministic failure diagnostics.

-   [x] **P2-04** Capture screenshots and traces with redaction controls.
    -   *Completed:* Integrated screenshot capture for explicit `screenshot` action and automatic
        diagnostic capture upon step failure. Evidence is persisted via `EvidenceStorage` with
        `artifact_type="screenshot"` and protected by project-level authorization.

-   [x] **P2-05** Add cancellation, timeout, and cleanup tests.
    -   *Completed:* Unit test suite (`tests/unit/test_adapter_web.py`, 28 tests) and integration
        pipeline test (`tests/integration/test_web_execution_pipeline.py`) covering successful flows,
        locators, selector injection rejection, SSRF navigation and redirect blocking, timeouts,
        popups, downloads, failure screenshots, cancellation, and guaranteed browser context cleanup.

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

-   [x] **P3-02** Add run state machine, worker leases, heartbeat, and
    recovery.
    -   *Completed:* Implemented worker lease management on `Run` (`lease_expires_at`), `POST /api/v1/runs/{run_id}/heartbeat`
        endpoint allowing active workers to renew leases, and `POST /api/v1/runs/recover-stale` endpoint with `run_service.recover_stale_runs()`.
        Stale/crashed workers automatically transition runs to `status='failed'`, emit `run_failed` timeline events, and never leave runs stuck.
    -   Acceptance: Verified via `tests/integration/test_worker_leases_and_idempotency.py`.

-   [x] **P3-03** Add idempotency key for run creation.
    -   *Completed:* Supported `Idempotency-Key` HTTP header and `body.idempotency_key` deduplication in `create_and_execute_run`.
        Duplicate requests return the identical existing Run instance without duplicate execution.
    -   Acceptance: Verified via `tests/integration/test_worker_leases_and_idempotency.py`.

-   [x] **P3-04** Add cancellation and bounded retry policy.
    -   *Completed:* Implemented `POST /api/v1/runs/{run_id}/cancel` with RBAC authorization (`owner`, `maintainer`),
        terminal-state conflict validation (409), idempotent cancellation, timeline event recording (`run_cancelled`),
        and audit trail. Bounded retry policy respects `idempotency_class` (`safe` and `idempotent` retried; `non_idempotent`
        never retried without explicit override). Worker checks cancellation status between step iterations.
    -   Acceptance: Verified with unit and integration tests (`tests/integration/test_runs.py`, `tests/integration/test_e2e_workflow.py`, `tests/unit/test_worker_isolation.py`).


-   [x] **P3-05** Add concurrency limits and per-project quotas.
    -   *Completed:* Implemented `count_active_runs_for_project` in `RunRepository` and enforced
        `MAX_CONCURRENT_RUNS_PER_PROJECT` in `run_service` (`execute_run_sync` and `create_and_enqueue_run`).
        When active queued/running executions reach the project quota, subsequent execution requests
        are rejected with HTTP 429 `RATE_LIMIT_EXCEEDED` and `Retry-After` header. Project-isolation
        ensures quotas are strictly scoped per project.
    -   Acceptance: Tested and verified via `tests/integration/test_concurrency_and_quotas.py`.

-   [x] **P3-06** Verify approval gate at run creation (TOCTOU).
    -   *Completed:* Enforced in `execute_run_sync` and `create_and_enqueue_run`. An unapproved version, draft version,
        or archived version is strictly rejected with `422 PLAN_NOT_APPROVED`. Atomic verification against snapshot.
    -   Acceptance: Tested in `tests/integration/test_runs.py` and `tests/integration/test_background_runs.py`.

-   [ ] **P3-07** Implement and verify container worker isolation and
    network egress policy.
    -   *Progress:* Execution worker isolation and runtime defense-in-depth implemented:
        - Centralized `ExecutionPolicy` and immutable `ExecutionLimits` bounding wall-clock (300s), step (30s), request/response sizes (5MB), and retries.
        - DNS rebinding TOCTOU defense via `RebindingSafeTransport` (IP-pinning outbound socket connections to pre-validated safe IPs).
        - Isolated ephemeral `ExecutionWorkspace` per run with storage quotas (100MB) and guaranteed `finally` teardown.
        - `RunEvidenceBudget` capping cumulative run evidence to 50MB and 100 artifacts.
        - Run cancellation detection halting step iteration cleanly.
        - Non-sensitive worker readiness diagnostics (`check_worker_readiness`).
        - Dockerfile hardened with non-root user (`UID 10001:10001`).
        - Docker Compose hardened with `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, CPU/RAM/PID limits, bounded `tmpfs`, and secret key isolation.
    -   *Remaining Infrastructure Requirement:* Kernel-level network packet egress filtering
        (iptables / cloud security group / egress gateway) to block egress at the network layer independently of application code before production release.
    -   Acceptance: worker container runs as non-root; network egress to
        private IPs and metadata addresses is blocked at the network
        layer; test confirms a blocked egress attempt from within the
        container is denied independently of application code.
    -   Note: this is required before Milestone 2 is declared complete.

---

## P4 --- user interface

-   [x] **P4-00** Implement API token issuance and revocation.
    -   *Completed:* Implemented `ApiToken` model, migration 0005, and `POST /api/v1/auth/tokens`, `GET /api/v1/auth/tokens`,
        and `DELETE /api/v1/auth/tokens/{token_id}` endpoints. Updated `get_current_user` auth middleware to support
        `Authorization: Bearer <token>` for non-browser clients (CI pipelines and CLI tooling).
    -   Acceptance: Verified via `tests/integration/test_auth_tokens.py`.

-   [x] **P4-01** Authentication and project navigation (UI).
    -   *Completed:* Configured `AppShell`, `Header` with dynamic project selector (`/api/v1/projects`)
        and health status badge, and `Sidebar` with accessible route highlighting across Dashboard,
        Test Runs, and Environments.
    -   Requires: CSS Modules design tokens and layout components configured (ADR-012).

-   [x] **P4-02** Project and environment management (UI).
    -   *Completed:*
        - Environment management: full vertical slice (`Environments.tsx`, `useEnvironments`) with allowlist and masked secret reference management.
        - Project Settings & Team Access: `Settings.tsx` enterprise management suite with "Team & Access" (role badges, member listing, "+ Add Member" modal, role update, remove member, last-owner guard), "API Tokens (CI/CD)" (token creation with expiry selection, raw token single-view display with clipboard copy, revocation, curl usage instructions), and "Audit Trail" (timeline with event-type filtering and JSON metadata inspector modal).
        - Integrated accessible routes in `App.tsx` (`/settings`, `/projects/:projectId/settings`) and navigation in `Sidebar.tsx`.
    -   Acceptance: Tested with backend integration tests (`test_project_settings_and_audit.py`) and Vitest frontend tests (`Settings.test.tsx`).

-   [x] **P4-03** Plan editor with schema validation feedback (UI).
    -   *Completed:*
        - Backend: Implemented `POST /api/v1/projects/{project_id}/plans/validate` and `POST /api/v1/plans/{plan_id}/validate`
          dry-run validation endpoints with comprehensive safety rules (checking adapter registration, action allowlists,
          code-injection token rejections, credential leakage checks, required field validation, and timeout/retry bounds).
          Added `PUT /api/v1/plans/{plan_id}` and `PUT /api/v1/plans/{plan_id}/versions/{version}` endpoints enforcing immutability.
        - Frontend: Built `PlanEditor.tsx` visual step builder with typed action fields (HTTP API and Web Playwright),
          step reordering (up/down), step cloning, deletion confirmation dialogs, inline error feedback, real-time validation,
          and clear indicators when editing approved plans that a new version will be branched.
    -   Acceptance: Tested with backend integration tests (`test_plan_builder_workflow.py`) and frontend tests (`PlanEditor.test.tsx`).

-   [x] **P4-04** Plan review/approval and version history (UI).
    -   *Completed:*
        - Backend: Implemented `POST /api/v1/plans/{plan_id}/submit-review`, `POST /api/v1/plans/{plan_id}/approve` (with TOCTOU
          version checking and audit tracking), `POST /api/v1/plans/{plan_id}/reject` (with audit reason), and `POST /api/v1/plans/{plan_id}/archive`.
          Enriched `GET /api/v1/plans/{plan_id}/versions` with step counts and version summaries.
        - Frontend: Created `Plans.tsx` plan catalog with search, status filters (`draft`, `pending_review`, `approved`, `rejected`, `archived`),
          and pagination. Built `PlanDetail.tsx` featuring version history selector, status badges, audit metadata, test step inspection,
          review submission, approval confirmation modal, and rejection modal with required reason.
    -   Acceptance: Tested with integration tests verifying TOCTOU approval, review/rejection workflows, immutable approved snapshots,
        and 8 Vitest frontend tests across `Plans.test.tsx` and `PlanEditor.test.tsx`.

-   [x] **P4-05** Run list, run timeline, step details, artifacts, and
    filters (UI).
    -   *Completed:*
        - Backend: Implemented `GET /api/v1/projects/{project_id}/dashboard` aggregated metrics
          endpoint and `GET /api/v1/projects/{project_id}/runs` with server-side filtering (status,
          plan, adapter, date range), sorting, tie-breaking, true count pagination, and cross-project authorization.
          Enriched `GET /api/v1/runs/{run_id}` with environment label, plan details, step actions, duration in ms,
          assertions breakdown, and failure categories (assertion failure, timeout, cancellation, policy blocked, execution error).
          Added `include_expired` query parameter to `GET /api/v1/runs/{run_id}/artifacts` and safe response headers to artifact downloads.
        - Frontend: Created `Dashboard.tsx` (metrics, pass-rate progress, adapter breakdown, activity
          timeline, recent runs), `Runs.tsx` (paginated run history, filter toolbar, sorting, live 4s polling
          for active runs), and `RunDetail.tsx` (step attempt breakdowns with failure category badges, duration,
          assertions evaluation list, timeline events, captured evidence table with retention status, safe lazy-loaded
          in-app evidence preview modal supporting images and multi-tab structured JSON request/response/assertion viewers,
          and safe binary download links).
    -   Acceptance: Tested with 8 integration tests in `test_artifacts.py`, 7 in `test_runs.py`, and 23 frontend Vitest tests.

-   [x] **P4-06** Loading, empty, error, blocked, and inconclusive
    states (UI).
    -   *Completed:* Implemented consistent accessible badges with non-color-only icons and labels
        (`passed`, `failed`, `running`, `queued`, `blocked`, `inconclusive`, `cancelled`), `Spinner`
        loaders, `ErrorMessage` with retry buttons, and contextual `EmptyState` across Dashboard,
        Runs, and RunDetail pages.

---

## P5 --- AI assistance

-   [x] **P5-00** Design and stub AI plan generation endpoint.
    -   *Completed:* Implemented `POST /api/v1/projects/{project_id}/plans/generate` returning 503 `AI_PROVIDER_NOT_CONFIGURED` when unconfigured. Defined `GeneratePlanRequest`, `BlockedStep`, and `GeneratePlanResponse` in `uitp_contracts.plan`.
    -   Acceptance: Verified 503 response and schemas via integration tests in `tests/integration/test_ai_plan_generation.py`.

-   [x] **P5-01** Provider interface with timeouts, budgets, and structured output.
    -   *Completed:* Implemented `AIProvider` protocol, `MockAIProvider` (for deterministic offline testing), and `HttpOpenAICompatibleProvider` with request timeouts and token bounds in `uitp_api.services.ai_provider`.
    -   Acceptance: Verified timeout handling, mock provider generation, and provider abstraction.

-   [x] **P5-02** Generate draft plans from acceptance criteria.
    -   *Completed:* Implemented `ai_plan_service.generate_plan` with untrusted prompt isolation (`<untrusted_user_intent>`, `<untrusted_spec>`), allowlisted actions, and `idempotency_class` on every step.
    -   Acceptance: Verified that steps are declarative, contain `idempotency_class`, and contain no executable code.

-   [x] **P5-03** Validate and explain rejected steps.
    -   *Completed:* Deterministic server-side post-validation filters dangerous steps (code injection, eval, unallowlisted actions, raw credential inputs) into `blocked_steps` with specific human-readable explanation reasons.
    -   Acceptance: Verified partial blocked step capture and rejection of completely unsafe plans with 422 `VALIDATION_ERROR`.

-   [x] **P5-04** Require approval before execution.
    -   *Completed:* All AI-generated plans are strictly saved as drafts with `requires_approval: true`. Execution attempts fail with 422 `PLAN_NOT_APPROVED` until human review and approval via `POST /api/v1/plans/{plan_id}/approve`. Built `AIGeneratePlanModal.tsx` in frontend with draft preview, blocked steps warnings, and links to the editor and approval flow.
    -   Acceptance: Tested approval gate rejection, subsequent approved execution, and 4 Vitest frontend tests.

-   [x] **P5-05** Generate evidence-grounded failure summaries (Task 8).
    -   *Completed:*
        - Implemented `evaluation_service.evaluate_and_persist_run` with single authoritative lifecycle trigger on terminal states (`passed`, `failed`, `blocked`, `cancelled`, `inconclusive`).
        - Enforced strict idempotency: cached evaluation report returned with `is_idempotent_replay: true` on duplicate triggers or redelivery.
        - Grounded deterministic root-cause analysis: classified into `confirmed_cause`, `probable_cause`, or `unknown_cause` (`[INSUFFICIENT_EVIDENCE]`), extracting failed assertions, expected vs actual values, HTTP status codes, UI locator errors, and dependency failure chains.
        - Requirement traceability matrix: mapped requirements to task outcomes with explicit `UNTESTED` diagnostic notes for unmapped requirements.
        - Optional token-bounded AI root-cause summarization with secret redaction (`_sanitize_for_prompt`) and automatic deterministic fallback. Invariant: AI never alters test results or converts failures into passes.
        - Reporting integration: deliverable exports (`/output/runs/{run_id}/execution_log.json` and `report.md`) include final evaluator assessment and requirement coverage matrix.
    -   Acceptance: Tested with unit tests (`tests/unit/test_evaluation_service.py`), integration tests (`tests/integration/test_post_run_evaluation_pipeline.py`), and multi-agent mission suites. Manual plans work offline without provider keys; prompt injection fixtures do not alter verdicts or policies.

---

## P6 --- security and release hardening

-   [x] **P6-01** Central redaction and artifact access controls.
    -   *Completed:*
        - Enforced strict project-membership authorization across all artifact endpoints (`GET /api/v1/runs/{run_id}/artifacts` and `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`). Cross-project requests return 404 to prevent tenant enumeration.
        - Validated artifact-to-run ownership preventing access even if artifact ID is swapped to a different run.
        - Verified 30-day retention policy enforcement: expired artifacts return 404 while preserving run history.
        - Audited every retrieval event with `ARTIFACT_ACCESSED` without leaking payload content or secrets.
        - Hardened downloads with security headers (`X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `X-Frame-Options: DENY`, `Cache-Control: private, no-cache, no-store, must-revalidate`). Blocked origin execution of active content (HTML, SVG).
    -   Acceptance: Verified with 8 integration tests in `tests/integration/test_artifacts.py`.

-   [x] **P6-02** Audit logs for approvals, environment changes, and
    runs.
    -   *Completed:*
        - All 15 audit event types defined in ARCHITECTURE.md §5a (`user_login`, `user_login_failed`, `role_change`, `project_created`, `project_archived`, `environment_created`, `environment_modified`, `secret_reference_updated`, `plan_created`, `plan_approved`, `plan_archived`, `run_created`, `run_cancelled`, `policy_blocked`, `artifact_accessed`) are verified in the database schema and covered by tests.
        - Implemented project-scoped `GET /api/v1/projects/{project_id}/audit-events` query endpoint with pagination and event_type filtering.
        - Emitted `role_change` audit events with actor attribution and metadata sanitization across team invitations, role alterations, and member removals.
        - Exposed interactive Audit Trail Explorer in UI (`Settings.tsx`) with metadata JSON inspection modal.
    -   Acceptance: Verified via `tests/integration/test_project_settings_and_audit.py`, `tests/unit/test_audit_service.py`, and `tests/integration/test_artifacts.py`.

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
