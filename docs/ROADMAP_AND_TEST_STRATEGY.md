# Roadmap and Test Strategy

## 1. Delivery approach

Deliver vertical slices that work end-to-end. Avoid building a large
abstract framework before a real adapter proves the design. Keep each
phase deployable and update docs when implementation decisions change.

The product has two named milestones:

-   **Milestone 1 — HTTP API slice:** demonstrable without a web
    browser, a queue, or an AI provider. The entire plan/run/evidence
    pipeline is proven through the HTTP API adapter alone.
-   **Milestone 2 — Full MVP:** adds the Playwright web adapter, the
    async worker queue, the React UI, and optional AI plan generation.
    This is the release that satisfies PRD §12 MVP acceptance criteria.

Do not claim full-MVP completion after only Milestone 1. Do not claim
Milestone 1 is complete unless all its exit criteria below are met.

## 2. Phases and exit criteria

### Phase 0 --- repository foundation

Deliver: monorepo layout, formatting/linting, typed contracts,
configuration, local developer instructions, CI skeleton (GitHub
Actions). Exit criteria: fresh checkout can install dependencies, run
checks, and start a health endpoint using documented commands. The
`make generate-types` command (or equivalent) runs without error and
produces TypeScript output matching the committed file.

### Phase 1 --- control plane basics

Deliver: authentication (session cookies; API tokens deferred to
Milestone 2), project membership/RBAC, project CRUD, environment
configuration, audit events (all types from ARCHITECTURE.md §5a),
database migrations. Exit criteria: authorization tests prove project
isolation; no plaintext secrets in the database; soft-delete enforced
for projects, plans, and environments.

### Phase 2 --- deterministic HTTP API testing

Deliver: declarative API plan schema with `idempotency_class` field,
HTTP adapter, safe target validation (SSRF application-layer), assertion
engine, timeouts, structured run/step events, basic report. Evidence
stored on local filesystem. Steps execute synchronously in-process
(Milestone 1 mode — documented development shortcut). Exit criteria:
successful, failed, blocked, timeout, and invalid-input paths have
tests; SSRF application-layer policy is enforced; a POST step with a
retry policy and `non_idempotent` class is attempted exactly once and
not automatically retried.

### --- Milestone 1 exit criteria (HTTP API slice) ---

All of Phase 0, Phase 1, and Phase 2 complete. Specific requirements:

-   A user can create a project, configure an allowed environment,
    create an HTTP API plan, approve it, execute it, and view the run
    result with evidence via the API.
-   A run against a draft, unapproved, or archived plan version is
    rejected with `PLAN_NOT_APPROVED`.
-   All application-layer security tests pass (SSRF, secret redaction,
    project authz, artifact authz).
-   Retry policy respects `idempotency_class`; non-idempotent steps are
    not retried automatically.
-   No web browser, no queue, no AI provider, and no React UI are
    required to reach this state.
-   The local worker's lack of container isolation is documented in the
    README and deployment guide.

### Phase 3 --- web UI testing (Playwright)

Deliver: Playwright adapter, locator-based actions, assertions,
screenshot/trace capture, redaction, worker cleanup. Initial
implementation runs synchronously in-process (same as Phase 2 mode).
Exit criteria: sample web application end-to-end flow passes; forced
assertion failure captures evidence; worker cleanup is verified. The
synchronous execution mode is explicitly marked as a development-only
shortcut in comments and documentation — it does not provide cancellation,
durable recovery, or production isolation.

### Phase 4 --- durable orchestration (async queue and workers)

Deliver: RQ queue, separate worker process, worker leases/heartbeats,
cancellation propagation, bounded retries, idempotency keys for run
creation, run history. Container-based worker isolation and network
egress policy introduced here (required for Milestone 2). Exit criteria:
worker restart does not silently mark an incomplete run passed; duplicate
run creation is handled; cancellation propagates to the adapter;
container isolation and egress policy are verified.

### --- Milestone 2 prerequisite checkpoint ---

Before continuing to Phase 5, verify:

-   Durable worker recovery is tested.
-   Network-layer egress policy is enforced in the worker container.
-   API tokens for non-browser clients are implemented (ADR-009).
-   TypeScript generation drift check passes in CI.

### Phase 5 --- user interface

Deliver: React UI using Shadcn/ui and Tailwind CSS. Authentication and
project navigation; project/environment management; plan editor with
schema validation feedback; plan version history (using
`GET /api/v1/plans/{plan_id}/versions`); plan approval flow; run list,
timeline, step details, artifacts, and filters; loading, empty, error,
blocked, and inconclusive states. Exit criteria: all UI flows work
without an AI provider key; plan version history is navigable; run
reports correctly distinguish all six outcome statuses.

### Phase 6 --- AI-assisted plan authoring

Deliver: provider abstraction with structured output, timeout, budget,
model configuration. AI draft generation via
`POST /api/v1/projects/{project_id}/plans/generate`; validation and
explanation of rejected steps; plan review and approval flow in UI;
evidence-grounded failure summaries. Exit criteria: invalid model output
is rejected by the schema validator; no plan executes without approval;
manually authored plans work when AI is unavailable; AI generation
returns `503` gracefully when no provider is configured; prompt
injection fixtures do not change policy or execution outcome.

### --- Full MVP (Milestone 2) exit criteria ---

All PRD §12 acceptance criteria met:

-   User can create a project, configure an environment, and save secret
    references.
-   User can generate or author a web/API plan, inspect it, edit it, and
    approve a version.
-   Invalid or unsupported operations are rejected before execution with
    a clear explanation.
-   Web and API runs execute in isolated workers with timeouts and
    cancellation.
-   Every step emits status, timestamps, and a traceable evidence
    reference.
-   Sensitive headers, cookies, tokens, and configured secrets are
    redacted in logs/artifacts.
-   Run reports distinguish failed, blocked, and inconclusive outcomes.
-   Unauthorized users cannot view or execute another project's runs.
-   Contract tests verify adapter capabilities and behaviour.
-   CI runs lint, type checks, unit tests, integration tests, and core
    end-to-end tests.
-   Container worker isolation and network egress policy are verified in
    staging.

### Phase 7 --- hardening and pilot

Deliver: rate limits, artifact retention lifecycle, secret scanning in
CI, monitoring dashboards, backups and restore drill, performance tests,
deployment guide (P6-05), operational runbook. Exit criteria: security
checklist in SECURITY_AND_AI_SAFETY.md §11 fully passes; restore drill
and pilot feedback are documented.

### Phase 8 --- expand adapters

Add future adapter types one at a time (mobile, desktop, CLI, event,
database, visual, accessibility). Each requires: capability
documentation, sandbox design, contract tests, and explicit limitations.
No adapter may be enabled without passing conformance tests. Do not
expand the MVP scope to include these adapters.

## 3. Testing pyramid

-   **Unit tests:** schema validation, policy decisions, state
    transitions, redaction, assertion functions, idempotency
    classification.
-   **Contract tests:** adapter metadata, capability negotiation,
    unsupported actions, timeout/cancel behaviour, evidence format,
    idempotency enforcement.
-   **Integration tests:** API + database + queue + object storage.
-   **End-to-end tests:** create project → approve plan → run → inspect
    evidence/report.
-   **Security tests:** authz, SSRF (application layer), injection,
    secret redaction, artifact access, rate limits, approval gate
    (TOCTOU), artifact enumeration prevention.
-   **Resilience tests (Milestone 2+):** worker termination, queue
    delay, duplicate requests, database outage, object storage failure.
-   **Performance tests:** concurrency, queue throughput, artifact
    volume, report queries.
-   **Manual exploratory tests:** UX, recovery, and target-specific edge
    cases.

## 4. Test fixtures

Maintain:

-   **`tests/fixtures/mock_http_api/`** — A deliberately simple ASGI
    HTTP server with predictable success, failure, timeout, schema-
    mismatch, and redirect routes. Required for all P1/Phase 2 adapter
    tests. Created in P1-00.
-   **`tests/fixtures/web_app/`** — A simple static web application
    with a login form, navigation, and assertion targets. Required for
    all P2/Phase 3 Playwright tests. Created in P2-00.
-   Test secrets that can be searched for in all logs/artifacts (to
    verify they are redacted).
-   Malicious HTML/API fixtures containing prompt-injection strings.
-   A local fake object store or test storage bucket.
-   Worker-crash and cancellation test harnesses (Milestone 2+).

## 5. Flakiness policy

-   Prefer stable semantic locators and explicit assertions over
    arbitrary sleeps.
-   Use bounded retries only for declared retryable conditions
    (`idempotency_class: safe` or `idempotent`).
-   Record every attempt and preserve initial failure evidence.
-   Do not convert a flaky pass into a clean pass without showing retry
    history.
-   Track flake rate by adapter, browser, environment, and test plan
    version.

## 6. Definition of done

A feature is done only when:

-   Acceptance criteria are met.
-   Authorization and error cases are covered.
-   Logs and artifacts are safe and useful (no secrets present).
-   Unit/integration/contract tests pass.
-   Docs and API contracts are updated.
-   UI includes loading, empty, error, and success states where
    applicable.
-   Configuration and rollback implications are documented.
-   The feature does not claim unsupported capability.
-   For any feature involving worker execution: the local/synchronous vs.
    production/async distinction is clearly documented in code comments
    and in the relevant task notes.
