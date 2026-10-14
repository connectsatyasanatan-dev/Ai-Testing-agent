# Architecture Decision Records

This file records baseline decisions. Revisit them when evidence from
implementation changes the trade-off.

## ADR-001 --- Adapter-driven core

**Status:** Accepted\
**Decision:** Keep target-specific execution inside adapters; the
orchestrator uses a common declarative plan/step contract.\
**Reason:** Allows different target types without coupling global run
management to one automation library.\
**Trade-off:** Requires a carefully versioned contract and conformance
suite.

## ADR-002 --- Start with web and HTTP API targets

**Status:** Accepted\
**Decision:** MVP production claims are limited to web and HTTP API
capabilities that pass tests.\
**Reason:** They validate core abstractions with accessible, widely used
automation techniques.\
**Trade-off:** Mobile, desktop, CLI, and event-driven testing are
planned, not initially delivered.

## ADR-003 --- AI proposes; deterministic policy disposes

**Status:** Accepted\
**Decision:** AI may draft plans and explain evidence, but validators,
permissions, risk rules, and assertions control execution and outcome.\
**Reason:** Model output can be incomplete, inaccurate, or manipulated
by target content.\
**Trade-off:** Human review and schema validation add steps but reduce
unsafe execution.

## ADR-004 --- Separate control plane and workers

**Status:** Accepted\
**Decision:** API requests enqueue runs; isolated workers execute them.\
**Reason:** Long-running browser/API tests need independent resource
limits, scaling, cancellation, and network policy.\
**Trade-off:** Queue, worker lifecycle, and recovery add operational
complexity.

## ADR-005 --- Monorepo for initial product

**Status:** Accepted\
**Decision:** Keep UI, API, contracts, adapters, tests, and docs in one
repository initially.\
**Reason:** A small team can coordinate shared schema changes and run
cross-component CI in one pull request.\
**Trade-off:** Toolchains and CI require clear boundaries. Split later
only when team/release ownership justifies it.

## ADR-006 --- PostgreSQL for shared deployment

**Status:** Accepted\
**Decision:** Use PostgreSQL for team/pilot deployment; SQLite may be
used for a local prototype only.\
**Reason:** Concurrent runs, multiple API/worker processes, and reliable
migrations need a production-grade shared database.\
**Trade-off:** Local development may require a container or a separately
documented lightweight mode.

## ADR-007 --- Declarative actions, not arbitrary code

**Status:** Accepted\
**Decision:** Expose allowlisted actions and typed inputs. Arbitrary
shell or script execution is not a default test primitive.\
**Reason:** Limits command injection, policy bypass, and worker
compromise.\
**Trade-off:** Some advanced use cases require carefully reviewed custom
adapters or explicitly approved extensions.

## ADR-008 --- Honest run outcomes

**Status:** Accepted\
**Decision:** Distinguish Passed, Failed, Blocked, Skipped,
Inconclusive, and Cancelled.\
**Reason:** A blocked test or missing evidence must not look like a
successful test.\
**Trade-off:** Reports and dashboards need more nuanced status handling.

## ADR-009 --- Authentication mechanism

**Status:** Accepted\
**Decision:** Use secure, HttpOnly, SameSite session cookies for the
browser UI. Issue short-lived opaque API tokens only for non-browser
clients (CI pipelines, CLI tooling) where cookie sessions are
impractical. Both paths share the same identity and permission model.\
**Reason:** Session cookies are the most mature and CSRF-resistant
default for a browser-first product. API tokens satisfy the requirement
that the HTTP API can be called without a browser, which makes the
Milestone 1 HTTP-only slice independently demonstrable.\
**Trade-off:** Two credential types require two code paths in auth
middleware. API tokens need rotation and revocation support.\
**Implementation order:** Implement session cookies first (P0-05); add
API token issuance before Milestone 2 UI work begins (P4-00).\
**Resolves open decision:** "Decide whether sessions or token-based auth
best fit deployment and integrations."

## ADR-010 --- Python-to-TypeScript type generation

**Status:** Accepted\
**Decision:** Generate TypeScript types from the FastAPI-produced OpenAPI
schema using `openapi-typescript`. The `packages/contracts/` Python
package is the single source of truth for all shared schemas. Generated
TypeScript output lives in `apps/web/src/generated/` and is committed to
the repository so the frontend never requires a running API to type-check.\
**Reason:** FastAPI + Pydantic produces an accurate OpenAPI schema at
zero extra cost. `openapi-typescript` is a single dev-only dependency
with no runtime footprint.\
**Trade-off:** Schema drift is only caught when the generation step runs.
Developers must regenerate types after changing Pydantic models and
commit the result.\
**Implementation note:** Add a `make generate-types` (or equivalent npm
script) to the repo root. CI must regenerate and fail if the committed
output differs from the freshly generated output.\
**Resolves open decision:** "Decide on frontend type generation strategy
before P0-02."

## ADR-011 --- Queue framework

**Status:** Accepted\
**Decision:** Use **RQ (Redis Queue)** for Milestone 2 durable
orchestration.\
**Reason:** RQ has the smallest operational and code surface of the three
candidates (RQ / Dramatiq / Celery). It requires only a Redis instance
and one Python dependency. For a solo developer, fewer moving parts to
debug and fewer abstraction layers between task definition and the queue
is a decisive advantage. Dramatiq is a credible alternative if typed
message schemas become important later; Celery carries more operational
weight than the MVP warrants.\
**Trade-off:** RQ lacks Celery's ecosystem and some of Dramatiq's
type-safety guarantees. If routing complexity or throughput becomes a
bottleneck, the orchestrator's queue abstraction layer allows swapping.\
**Constraint:** Milestone 1 runs all steps synchronously in-process (a
plain function call or thread executor). RQ is not introduced until
P3-01. This synchronous mode is an **explicit, documented development
shortcut** — it does not provide production worker isolation or durable
recovery.\
**Resolves open decision:** "Select one queue framework after a short
operational spike."

## ADR-012 --- UI styling and component system

**Status:** Accepted (Updated)\
**Decision:** Use vanilla CSS with design tokens (`src/styles/variables.css`)
and scoped **CSS Modules** (`*.module.css`) for UI components, preserving
the existing implementation without introducing Tailwind CSS or Shadcn/ui.\
**Reason:** The MVP frontend components (`frontend/src/components/ui/`) have
already been implemented using React, TypeScript, and CSS Modules. Preserving
this approach avoids an unnecessary frontend rewrite, eliminates external
utility-compiler build dependencies, and keeps styling lightweight and directly
inspectable. Unstyled accessible primitives (e.g., Radix UI primitives) can be
introduced individually in Milestone 2 if complex interactive patterns (such as
accessible dialogs or comboboxes) require them.\
**Trade-off:** Component styling is authored manually without utility class
abstractions or pre-built widget libraries. Visual and layout consistency relies
on maintaining design tokens in `variables.css`.\
**Resolves open decision:** "Select UI component library and charting
package based on accessibility and maintenance."

## ADR-013 --- Default artifact retention period

**Status:** Accepted\
**Decision:** Establish a default artifact retention period of **30 days**.\
**Reason:** Balances local/pilot disk usage and privacy considerations against
retaining sufficient diagnostic history (logs, screenshots, traces) for recent
test runs.\
**Design rules & constraints:**
- The retention period must be configurable via application settings (e.g.,
  proposed `UITP_ARTIFACT_RETENTION_DAYS` in `config.py`, defaulting to 30 days).
- Automatic deletion must strictly enforce project authorization boundaries and
  record audit events (`artifact_purged` / audit trail).
- Automatic deletion must not erase run/attempt history or tamper with prior
  test run outcomes; only expired underlying artifact binaries/payloads are
  eligible for pruning.
- Implementation of automated cleanup scheduling and physical file deletion is
  deferred to release hardening (P6-01 / Phase 7).\
**Resolves open decision:** "Default artifact retention period: Define after
privacy and operational review. Resolve before P0-04 (database migrations)."

## ADR-014 --- Worker isolation boundaries and centralized execution policy

**Status:** Accepted\
**Decision:** Harden worker execution through a centralized `ExecutionPolicy` layer,
pre-validated IP connection pinning (`RebindingSafeTransport`), per-run ephemeral workspaces
(`ExecutionWorkspace`), run evidence quotas (`RunEvidenceBudget`), and non-root
container configurations (`UID 10001`, dropped Linux capabilities, bounded tmpfs)
before enabling browser automation or third-party adapters.\
**Reason:** Adapters must not define their own disparate resource limits or connection policies.
Treating all target responses, redirect locations, and DNS resolutions as potentially adversarial
prevents SSRF, DNS rebinding TOCTOU attacks, disk space exhaustion, and container escape.\
**Trade-offs & Constraints:**
- Application-layer IP pinning cannot replace kernel-level network packet filtering (iptables / firewall),
  which remains a requirement for staging/production environments with untrusted code.
- Docker containers run non-root and require pre-allocated directory ownership for artifact volumes.

## ADR-015 --- Declarative Test Plan Immutability, Versioning, and Approval Workflow

**Status:** Accepted\
**Decision:** Test plans enforce strict version immutability across the entire lifecycle:
1. Approved plan versions cannot be edited in place. Editing an approved plan automatically branches a new draft version.
2. Attempting to directly modify an approved version via version-specific APIs returns `409 CONFLICT (PLAN_VERSION_IMMUTABLE)`.
3. Past test runs capture an immutable snapshot of the approved plan version at execution time; subsequent drafts or version increments never mutate past run snapshots.
4. Transitions to `approved` status require explicit role privileges (`owner` or `maintainer`) and record the approving user's identity and timestamp.
5. Review workflow supports `draft -> pending_review -> approved | rejected` state transitions. Rejection requires a mandatory or optional audit reason.
6. Plan actions are strictly declarative and typed against adapter registries. Arbitrary shell, Python, or JavaScript execution within plan steps is forbidden.
**Reason:** Compliance, deterministic reproducibility, and security require that what was reviewed and approved cannot be tampered with or retroactively altered after execution.\
**Trade-offs & Constraints:**
- Branching a new draft on edit requires users to re-submit for review and approval if approval policies are enforced.
- Soft-deletion (`archived`) preserves referential integrity for historic runs and audit trails.

## ADR-016 --- Vendor-Agnostic AI Provider Abstraction and Deterministic Safety Boundary

**Status:** Accepted\
**Decision:**
1. AI providers implement the vendor-neutral `AIProvider` protocol, supporting configurable HTTP endpoints (OpenAI-compatible, Azure, vLLM, Ollama) and a deterministic in-memory `MockAIProvider` for test suites and offline CI.
2. Credentials (`UITP_AI_API_KEY`) are stored in server-side configuration/SecretStr and never transmitted to the client, logged, or recorded in audit trails.
3. Prompt injection isolation: user objectives and specifications are encapsulated inside `<untrusted_user_intent>` and `<untrusted_spec>` tags with explicit system instructions prohibiting execution overrides.
4. AI output is strictly untrusted data: generated actions and assertions undergo deterministic post-generation validation. Any steps attempting code execution (`eval`, `exec`, `subprocess`), unallowlisted actions, raw credential embeddings, or schema violations are filtered into `blocked_steps` and excluded from the test plan.
5. Mandatory Human Approval Gate: AI-generated plans are unconditionally persisted as `status="draft"` with `requires_approval=True`. Attempting to execute an AI-generated draft before explicit approval is blocked with `422 PLAN_NOT_APPROVED`.
6. Graceful Degradation: If no AI provider is configured, the endpoint returns `503 AI_PROVIDER_NOT_CONFIGURED` without affecting manual test plan authoring or test execution.
**Reason:** Ensures security against prompt injection and model hallucinations while preventing vendor lock-in and avoiding paid dependencies during normal CI.\
**Trade-offs & Constraints:**
- AI generation cannot execute directly: every generated plan requires human review and explicit approval.
- Post-validation strictly drops unsupported actions rather than attempting heuristic repair.

## ADR-017 --- Secure Cross-Layer Runtime Context Propagation

**Status:** Accepted\
**Decision:**
1. **Run-Scoped Lifetime & Tenancy Isolation:** Each mission execution initializes an ephemeral `RuntimeContext` strictly bound to the specific `run_id` and `project_id`. State is never shared across runs, tenants, or worker processes.
2. **Ancestor-Only Scoping:** Tasks can only consume context variables exported by explicit dependency ancestors (direct or transitive parents via `depends_on`). Attempting to read data from an independent or non-ancestor task is rejected before dispatch with `DisallowedContextReferenceError`.
3. **Strict Declarative Syntax:** Only approved `${context.<var_name>}` expressions are supported. Arbitrary template evaluation, scripting engines, or unapproved namespaces are rejected with `InvalidContextExpressionError`.
4. **Pre-Adapter Validation Gate:** Missing variables, malformed expressions, disallowed references, and type mismatches (e.g. interpolating non-scalar structures into string templates) are validated immediately before adapter dispatch. Any violation terminates the task with a structured error, preventing partial or corrupt execution, and cascades `BLOCKED` status to downstream tasks.
5. **Deterministic Export Extraction:** Exports are extracted only from successful task outputs. If an exported JSONPath resolves to null or is missing, the producer task fails with `MissingContextExportError` and publishes nothing. Duplicate export declarations are rejected at plan validation and runtime to prevent overwrite races.
6. **Multi-Layer Secret Redaction:** Context variables marked with `is_secret=True` are persisted in the `run_contexts` database table with `value="[REDACTED]"`. Unredacted values exist only in transient memory during run execution and are scrubbed from evidence artifacts, response logs, error messages, and timeline events prior to persistence.
7. **Concurrency-Safe Mutations:** In-memory context operations are serialized with an asyncio lock to ensure deterministic parallel task completion and export publication.
**Reason:** Enables rich cross-layer multi-agent workflows (e.g. API authentication generating tokens consumed by downstream API checks and Playwright UI tests) while maintaining strict least-privilege scoping, zero credential leakage, and full backwards compatibility with single-plan execution.\
**Trade-offs & Constraints:**
- Tasks must declare explicit dependency edges if they intend to share context; hidden coupling is blocked.
- Secret context variables cannot be retrieved in plaintext from the database after run completion.

## ADR-018 --- Evidence-Based Execution Log and Final Report Export

**Status:** Accepted\
**Decision:**
1. **Authoritative Persistence:** Export generation draws strictly from real, persisted execution database records (`Run`, `DAGTask`, `DAGTaskAttempt`, `StepAttempt`, `EvidenceArtifact`, `RunEvent`), never fabricated data or transient unpersisted state.
2. **Dual-Path Collision Safety:** Output files are written to both a collision-safe per-run directory (`output/runs/<run_id>/execution_log.json` and `output/runs/<run_id>/report.md`) and the deliverable root paths (`output/execution_log.json` and `output/report.md`). Concurrent runs cannot overwrite each other's historical records.
3. **Atomic Write Strategy:** All generated JSON and Markdown deliverables and copied evidence artifacts are written to temporary files (`<name>.tmp.<uuid>`) with explicit flush and `fsync`, followed by atomic replacement (`os.replace`). Exporter interruptions or crashes never leave corrupt or partially-written files.
4. **Strict Secret Redaction:** Exported payloads, headers, context, and timeline events undergo multi-pass sanitization via `RedactionEngine` and `redact_sensitive_values`. Credentials, tokens, and authorization headers are replaced with `"[REDACTED]"`.
5. **Evidence Grounding & Uncertainty Classification:** Root-cause diagnostics in both `execution_log.json` and `report.md` are derived directly from actual assertion results, HTTP status codes, schema validation failures, or exception traces. If telemetry is inconclusive, the report explicitly emits `[INSUFFICIENT_EVIDENCE]` rather than inferring hypothetical causes.
6. **Requirement Traceability Mapping:** Requirements are mapped to verifying tasks and evaluated against actual outcomes (`PASSED`, `FAILED`, `BLOCKED`, `UNTESTED`).
**Reason:** Fulfills assignment deliverable requirements (`/output/execution_log.json`, `/output/report.md`, and failure screenshots) with enterprise-grade integrity, tenant isolation, and verifiable evidence.\
**Trade-offs & Constraints:**
- Copying screenshots into export directories consumes disk space bounded by the execution evidence budget.
- Root path files (`/output/execution_log.json`) reflect the most recently executed run.

## ADR-019 --- Requirement-Driven Smoke and Regression Test Selection

**Status:** Accepted\
**Decision:**
1. **Source Requirements Authoritative Priority:** Task priority (`P0`, `P1`, `P2`, `P3`), smoke eligibility (`is_smoke`), and regression eligibility (`is_regression`) are deterministically derived from explicit priority and criticality tags in `requirements.md` and `scenarios.json`. Missing priorities predictably default to `P1` (non-smoke).
2. **Rejection of Title-Based Escalations:** Tasks titled or named containing words like 'smoke' are NOT granted P0 status or smoke eligibility without explicit P0 source requirements. AI model-proposed classifications are reconciled against source requirements and deterministically corrected or downgraded.
3. **Prerequisite Dependency Closure Invariant:** When executing a subset (`suite_type="smoke"` or `"regression"`), transitive upstream dependencies outside the target subset are automatically resolved as `prerequisites`. Dependencies are never silently omitted, guaranteeing runtime context variable resolution without broken dependencies.
4. **Pruning & Traceability Separation:** Tasks outside the targets and prerequisites closure are marked with `TaskStatus.SKIPPED`, emitted as `DAGTaskAttempt(status="skipped")` and `RunEvent(event_type="task_skipped")`. In reports and the Requirement Traceability Matrix, skipped tasks are marked `UNTESTED` with finding `'Excluded from <suite> suite execution'`, maintaining full audit traceability without obscuring untested scope.
5. **Full Backwards Compatibility:** Existing plans and single-plan runs lacking priority fields default cleanly to `priority=P1`, `is_smoke=False`, `is_regression=True`, executing in full suite mode by default.
**Reason:** Ensures automated smoke testing for critical P0 paths and dynamic regression selection that reliably reflects requirement metadata while preserving dependency integrity and secret-safety.\
**Trade-offs & Constraints:**
- Smoke runs may execute non-smoke prerequisite tasks (e.g. database seeding, user authentication setup) necessary to enable downstream P0 tests.

## ADR-020 --- Deterministic OpenAPI Analysis for API Test Planning

**Status:** Accepted\
**Decision:**
1. **Deterministic Parser and Pre-Validation Gate:** OpenAPI specifications (JSON or YAML) are parsed and normalized deterministically before calling any LLM. Malformed syntax, missing version definitions, unsupported versions (< 3.0, Swagger 2.0), and specs exceeding size limits (256 KB) are rejected immediately with HTTP 422 `VALIDATION_ERROR`.
2. **SSRF Defense via External Reference Rejection:** In compliance with platform security invariants, external `$ref` pointers (e.g. `http://...`, `https://...`, `file://...`) are prohibited by security policy to prevent server-side request forgery and credential exfiltration. Only local JSON pointers (`#/components/schemas/...`, `#/paths/...`) are resolved.
3. **Circular & Deep Reference Safety:** Local schema references are traversed with RFC 6901 decoding and bounded recursion limits (max depth 20) with visited pointer tracking to prevent infinite loops on recursive schemas.
4. **Structured Token-Efficient Normalization:** OpenAPI specs are normalized into typed Pydantic contracts (`NormalizedOpenAPISpec`, `NormalizedOperation`, `OpenAPIRequestBody`, `OpenAPIResponse`, `OpenAPIParameter`). Rather than unparsed, truncated text dumps, the supervisor prompt receives structured Markdown summaries (`<analyzed_openapi>`) detailing operations, methods, auth requirements, parameters, schemas, and candidate test intents without silent semantic truncation.
5. **Grounded Test Candidate Generation:** Structured test candidates are generated deterministically across categories:
   - `POSITIVE_HAPPY_PATH`: Documented 2xx status codes and response schema validations.
   - `REQUIRED_FIELD_VALIDATION`: Negative test cases systematically omitting required request body fields or query parameters.
   - `BOUNDARY_VALIDATION`: Testing invalid enum values and boundary limits.
   - `DOCUMENTED_ERROR`: Tests targeting documented 4xx codes (e.g. 404 for item IDs).
   - `SECURITY_AUTH`: Tests calling authenticated endpoints without credentials expecting HTTP 401.
   - `LIFECYCLE_SEQUENCE`: Detecting CRUD resource collections and item endpoints.
6. **Explicit Uncertainty Labeling:** Any candidate expectation not strictly defined in the specification is flagged with `is_inferred = True` and listed in `unresolved_expectations`. The parser never invents hypothetical business rules absent from the spec.
7. **No Direct Execution:** The parser and candidate generator perform no HTTP requests and bypass no target allowlists or approval gates.
**Reason:** Makes OpenAPI-driven test generation structured, reliable, and grounded while defending against prompt-injection, SSRF, and model hallucination.\
**Trade-offs & Constraints:**
- Specifications using external multi-file `$ref` must be bundled into self-contained OpenAPI 3.x documents before submission.
- Complex nested polymorphic schemas (`oneOf`, `anyOf`) are shallowly merged; complete schema validation is enforced at test execution time via the assertion engine.

## ADR-021 --- Bounded Recovery and Safe UI Locator Fallbacks

**Status:** Accepted\
**Decision:**
1. **Transient vs Permanent Error Classification:**
   - Transient infrastructure and network failures (`NETWORK_ERROR`, `ADAPTER_TIMEOUT`, HTTP 502, 503, 504, 429) are retryable subject to bounded attempts and backoff.
   - Permanent failures (client HTTP 4xx except 429, schema validation errors, unsupported actions, policy blocks, target allowlist blocks, and deterministic assertion failures) are strictly non-retryable. Business assertion failures are never masked by retries.
2. **Safe Idempotency Bounds:**
   - Steps marked `idempotency_class="non_idempotent"` are attempted exactly once and strictly prohibited from retries unless the plan author explicitly sets `allow_non_idempotent_retry=True`.
   - Exponential backoff (`base * 1.5^(attempt-1)`) is bounded between [0.1s, 30.0s] with a hard ceiling of 5 attempts (`ABSOLUTE_MAX_ATTEMPTS`).
3. **Bounded AI Response Repair:**
   - AI-generated structured model responses are validated against strict Pydantic schemas (`DAGTaskSchema`, `MissionDAGPlan`).
   - On malformed JSON or schema violation, bounded repair feedback re-prompts the model up to 2 repair attempts (3 total calls) containing only sanitized schema diagnostic paths (`field: error`).
   - Repair prompts strictly exclude secrets, raw tokens, and credentials. Model output containing executable code tokens (`__import__`, `eval(`, `os.system`) is rejected.
   - If repair attempts are exhausted, the request fails with HTTP 422 `VALIDATION_ERROR`. Invalid model outputs are never silently converted into successful plans.
4. **Conservative UI Locator Fallbacks:**
   - Locators support fallback resolution across ranked, allowlisted strategies: `test_id` -> `role` -> `label` -> `placeholder` -> `text` -> `css`, capped at 3 fallback attempts.
   - **Unambiguous Match Invariant:** A locator is only accepted if it matches exactly one element (`count == 1`). If multiple elements match (`count > 1`), `AmbiguousLocatorError` is raised immediately. Blindly clicking the first matching element is strictly prohibited to prevent destructive or unintended actions.
   - If a fallback succeeds, telemetry logs `recovered_via_fallback=True`, `fallback_strategy`, and `locator_strategy`.
   - Post-action assertions are strictly preserved; recovering an element locator never masks or bypasses a failed business assertion.
5. **Diagnostic Failure Evidence:**
   - On locator resolution failure, timeout, or action error, the adapter captures a screenshot artifact and a sanitized DOM snapshot (with passwords and sensitive inputs redacted to `[REDACTED]`) bounded to 4KB.
   - Deterministic fallbacks satisfy requirements without adding speculative, untrusted AI locator repair in the browser.
**Reason:** Prevents flakiness from transient network drops and minor UI selector alterations while rigorously defending against prompt injection, destructive non-idempotent re-execution, credential leakage, and assertion masking.\
**Trade-offs & Constraints:**
- Fallbacks are bounded to 3 candidates per action to prevent unbounded resolution latency.
- Ambiguous matches fail fast rather than guessing element order.

## ADR-022 --- Post-Run Evaluation and Evidence-Based Root Cause Analysis

**Status:** Accepted\
**Decision:**
1. **Authoritative Evaluation Trigger & Terminal Lifecycle:**
   - Test run evaluation is automatically triggered across all runner paths (`DAGTaskScheduler` in API, worker execution runner, single-plan execution, and run cancellation) when a run reaches terminal status (`passed`, `failed`, `blocked`, `cancelled`, `inconclusive`).
   - Evaluation is decoupled from worker internals and executed through `evaluation_service.evaluate_and_persist_run`.
2. **Strict Idempotency:**
   - To guard against worker job redelivery, multiple completion messages, or repeated API calls, `evaluate_and_persist_run` checks for prior `RunEvent(event_type="evaluation_completed")`.
   - If an existing evaluation record is present and not explicitly forced (`force_reevaluate=False`), the cached `MissionEvaluationReport` is returned with `is_idempotent_replay=True`, preventing duplicate events and database mutations.
3. **Deterministic Verdict & State Transition Rules:**
   - Overall run verdict is computed deterministically:
     - Any task `failed` -> Run status `FAILED`.
     - Any task `blocked` (with no failed tasks) -> Run status `BLOCKED`.
     - Any task `cancelled` or run explicitly cancelled -> Run status `CANCELLED`.
     - All tasks `passed` -> Run status `PASSED`.
     - Otherwise -> `INCONCLUSIVE`.
4. **Requirement Traceability Coverage:**
   - Maps requirement IDs to task outcomes: all tasks passed -> `PASSED`, any task failed -> `FAILED`, any blocked -> `BLOCKED`.
   - Requirements declared in specification metadata without executable task coverage are unambiguously labeled `UNTESTED` with diagnostic notes explaining absence of executable tasks.
5. **Evidence-Grounded Root Cause Analysis:**
   - Analyzes deterministic telemetry first: failed assertions (expected vs actual), HTTP response status codes, transient network vs permanent error classifications, UI locator ambiguity, screenshot artifacts, DOM dumps, and dependency blocking chains.
   - Categorizes causes into `confirmed_cause` (assertion mismatch, locator validation error, prerequisite failure), `probable_cause` (timeout, network drop), or `unknown_cause` (`[INSUFFICIENT_EVIDENCE]`).
6. **Optional AI Summarization with Hard Invariants:**
   - When an LLM provider is configured, it synthesizes an executive summary and plausible hypotheses.
   - **Critical Invariant:** AI output never alters test verdicts, counts, or passes/fails.
   - **Secret Redaction:** Input prompts are sanitized via `_sanitize_for_prompt` to redact bearer tokens, passwords, and sensitive credentials.
   - **Graceful Fallback:** If the model fails, returns malformed JSON, or times out, the evaluator seamlessly falls back to deterministic diagnostics without failing the evaluation.
7. **Reporting & Deliverable Integration:**
   - Automatically exports or refreshes `/output/runs/{run_id}/execution_log.json` and `/output/runs/{run_id}/report.md` upon evaluation completion, ensuring deliverables reflect final evaluator assessments and root cause diagnostics.
**Reason:** Ensures automated post-run analysis is strictly truthful, evidence-grounded, idempotent, secure against prompt manipulation, and observable.\
**Trade-offs & Constraints:**
- AI hypotheses are explicitly labeled `is_ai_generated: true` and cannot overwrite deterministic facts.
- Evaluation failures emit `evaluation_failed` events and preserve the original execution run state.

## Open decisions

-   None pending for MVP Milestone 2.



