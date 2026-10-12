# Product Requirements Document (PRD)

## Universal Autonomous AI Testing Platform

**Status:** Proposed baseline\
**Version:** 1.0\
**Date:** 12 October 2026

## 1. Executive summary

Build a platform that accepts a target application, test intent,
environment constraints, and credentials supplied through secure
references; creates a reviewable test plan; executes tests through
specialized adapters; collects evidence; and produces a traceable
report. AI can help translate plain-language intent into structured test
steps, propose edge cases, interpret failures, and summarize results.
The platform---not the language model---owns permissions, execution
state, assertions, and final status.

## 2. Problem statement

Teams often maintain separate tools and workflows for UI tests, API
checks, regression tests, and exploratory testing. Test authoring is
time-consuming, coverage gaps persist, and failures can be hard to
diagnose. A generic agent that directly controls arbitrary systems is
risky and unreliable. The product should unify orchestration and
reporting while keeping target-specific behavior inside adapters with
explicit capabilities.

## 3. Vision and product principles

1.  **Adapter-first:** a stable core supports multiple kinds of targets.
2.  **Evidence over confidence:** every result links to observable
    evidence and deterministic checks where possible.
3.  **Human-governed autonomy:** users set boundaries; risky actions
    require approval.
4.  **Honest capability reporting:** unsupported or inconclusive is
    never labeled passed.
5.  **Least privilege:** each run receives only the access it needs.
6.  **Reproducibility:** record versions, environment, plan,
    configuration, and artifacts.
7.  **Incremental universality:** prove the architecture with web and
    HTTP API adapters before expanding.

## 4. Target users

-   QA engineers who author and maintain tests.
-   Developers who need repeatable pre-merge and regression checks.
-   Engineering leads who need quality trends and release gates.
-   SRE/platform teams who need controlled test execution.
-   Product teams who want to turn acceptance criteria into reviewable
    test plans.

## 5. Jobs to be done

-   Turn requirements into a test plan that a human can inspect.
-   Run approved tests against a selected environment.
-   Detect regressions and provide enough evidence to debug.
-   Re-run failed or flaky tests under explicit retry rules.
-   Compare runs and see coverage, failure trends, and unresolved risks.
-   Add a new target technology by implementing an adapter rather than
    rewriting the orchestrator.

## 6. Scope

### 6.1 MVP --- required

-   User authentication and role-based access.
-   Projects and environment profiles.
-   Secure references to secrets; never store plaintext secrets in run
    logs.
-   Test intent input (plain language plus optional structured steps).
-   AI-generated draft plan with validation and human review.
-   Web adapter for browser-based flows using Playwright.
-   HTTP API adapter for requests, schemas, status codes, and response
    assertions.
-   Run queue and isolated worker execution.
-   Structured steps, assertions, timeouts, bounded retries,
    cancellation.
-   Screenshots, browser traces, request/response metadata with
    redaction.
-   Run report with Passed, Failed, Blocked, Skipped, and Inconclusive
    outcomes.
-   Audit log, run history, basic search and filtering.
-   API and UI for creating, reviewing, launching, and inspecting runs.
-   Unit, integration, adapter contract, and end-to-end tests.

### 6.2 Later releases

-   Mobile automation adapter.
-   Desktop application adapter.
-   CLI and shell-command adapter.
-   Event-driven/message queue adapter.
-   Database validation adapter (read-only by default).
-   Visual regression and accessibility checks.
-   CI provider integrations and release-gate policies.
-   Team collaboration, approvals, analytics, and adapter marketplace.

### 6.3 Explicit non-goals for MVP

-   Guaranteed compatibility with every application or operating system.
-   Unrestricted autonomous production changes or destructive actions.
-   Replacing all specialist test tools.
-   Treating model-generated assertions as ground truth without
    validation.
-   Training a foundation model.
-   Storing or replaying user credentials in prompts or ordinary logs.

## 7. Core user journeys

### Journey A: create a project

1.  User creates a project and registers a target environment.
2.  User selects allowed adapters and supplies a base URL or target
    descriptor.
3.  Secrets are saved in a secret store or configured as references.
4.  System validates connectivity without performing destructive
    actions.

### Journey B: generate and review a plan

1.  User provides requirements, acceptance criteria, and optional
    examples.
2.  AI proposes structured steps, expected outcomes, and risk labels.
3.  A deterministic schema validator rejects malformed or disallowed
    operations.
4.  User edits and approves the plan.
5.  The approved plan is versioned.

### Journey C: execute and investigate

1.  User starts a run against a chosen environment.
2.  Policy engine checks permissions, target allowlists, and operation
    risk.
3.  Scheduler queues the run to an isolated worker.
4.  Adapter executes actions and emits structured events/evidence.
5.  Assertions determine step outcomes; the orchestrator computes run
    outcome.
6.  UI presents timeline, artifacts, redacted logs, and likely failure
    causes.
7.  User can rerun with the same plan/configuration or create a revised
    version.

## 8. Functional requirements

-   **FR-01 Identity:** authenticated users and roles such as Owner,
    Maintainer, Viewer.
-   **FR-02 Projects:** create, update, archive, and authorize access to
    projects.
-   **FR-03 Environments:** define target endpoints and permitted target
    domains; isolate configuration by environment.
-   **FR-04 Test plans:** create manually or from AI drafts; version and
    approve plans.
-   **FR-05 Plan validation:** validate schema, adapter capability,
    policy, timeouts, and risk before execution.
-   **FR-06 Execution:** queue, start, cancel, timeout, and track runs
    with a state machine.
-   **FR-07 Adapters:** common contract for validation, execution,
    capabilities, health, and evidence.
-   **FR-08 Assertions:** deterministic assertion engine for status,
    text, locator state, JSON paths, and timing bounds.
-   **FR-09 Evidence:** store metadata and artifacts with access
    controls, retention, and redaction.
-   **FR-10 Reporting:** show status, step results, duration, artifacts,
    retries, and failure classification.
-   **FR-11 Auditability:** record actor, action, target, plan version,
    policy decision, and timestamps.
-   **FR-12 AI assistance:** produce draft plans, candidate tests, and
    summaries; never bypass policy.
-   **FR-13 Safety:** block unapproved destructive operations and
    targets outside allowlists.
-   **FR-14 Observability:** structured logs, metrics, traces, queue
    depth, worker health, and failure rates.
-   **FR-15 Extensibility:** adapters register capability metadata and
    pass contract tests.

## 9. Run and step statuses

-   **Passed:** all required assertions passed.
-   **Failed:** one or more required assertions failed.
-   **Blocked:** execution could not safely or technically
    begin/continue.
-   **Skipped:** intentionally not run under a recorded rule.
-   **Inconclusive:** evidence was insufficient to decide.
-   **Cancelled:** user or policy cancelled the run. A retry must not
    erase the history of the original attempt. A run may be considered
    successful only when all required steps meet the configured success
    policy.

## 10. Non-functional requirements

-   **Security:** least privilege, secret redaction, target allowlists,
    isolated workers.
-   **Reliability:** durable run state; workers can restart without
    silently duplicating non-idempotent actions.
-   **Performance:** show queue/run progress promptly; use configurable
    timeouts rather than universal fixed limits.
-   **Scalability:** separate API/control plane from workers; allow
    horizontal worker scaling.
-   **Maintainability:** typed contracts, adapter conformance tests,
    documented state machine.
-   **Portability:** local development via containers where practical;
    avoid cloud-only assumptions in core abstractions.
-   **Accessibility:** keyboard-operable UI and meaningful status text.
-   **Auditability:** immutable run events or append-only audit records.
-   **Privacy:** data minimization and configurable artifact retention.

## 11. Success metrics

Initial targets should be baselined during pilot rather than treated as
guaranteed: - Percentage of runs with complete evidence for every
executed step. - Percentage of AI plans accepted after human review. -
False-pass rate found by independent validation. - Median time from
failure to useful diagnosis. - Flake rate by adapter and test. - Adapter
conformance pass rate. - Worker crash/recovery rate. - Percentage of
secrets detected in artifacts (target: zero). - Time to add a conformant
adapter.

## 12. Acceptance criteria for MVP

-   A user can create a project, configure an allowed environment, and
    save secret references.
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
-   Contract tests verify adapter capabilities and behavior.
-   CI runs lint, type checks, unit tests, integration tests, and core
    end-to-end tests.

## 13. Key risks and mitigations

-   **AI invents steps:** validate against schemas and adapter
    capabilities; human approval.
-   **Prompt injection from target content:** treat page/API content as
    untrusted data, not instructions.
-   **Destructive actions:** default-deny, risk labels, approvals, and
    sandbox environments.
-   **Flaky tests:** explicit waits, deterministic assertions, trace
    capture, bounded retries.
-   **Secret leakage:** centralized redaction, restricted artifact
    access, secret scanning.
-   **Universal scope becomes too large:** enforce phased releases and
    adapter-level exit criteria.
