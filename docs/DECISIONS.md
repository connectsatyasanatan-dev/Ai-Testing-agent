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

## Open decisions

-   **AI provider/model:** Select based on structured-output quality,
    privacy terms, latency, and budget. Resolve before P5-01.
