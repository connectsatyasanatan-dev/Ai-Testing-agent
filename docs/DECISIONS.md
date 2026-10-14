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

## Open decisions


-   **Model-assisted Failure Root Cause Summaries (P5-05):** Define evidence-grounded prompt schema referencing only sanitized step attempt logs and assertion failure traces.

