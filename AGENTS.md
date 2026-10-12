# Instructions for Coding Agents

## Mission

Implement the Universal Autonomous AI Testing Platform as an
adapter-driven testing system. Optimize for correctness, security,
evidence, and maintainability---not impressive demos that bypass
controls.

## Read first

1.  `README.md`
2.  `PRD.md`
3.  `ARCHITECTURE.md`
4.  `ADAPTERS_AND_CAPABILITIES.md`
5.  `SECURITY_AND_AI_SAFETY.md`
6.  `TASKS.md`
7.  `DECISIONS.md`

## Non-negotiable rules

-   Do not claim a feature is implemented unless code and tests
    demonstrate it.
-   Do not mark unsupported or inconclusive work as passed.
-   Do not let model output bypass schemas, permissions, target
    allowlists, or risk policies.
-   Never store plaintext secrets in the database, prompts, logs, or
    artifacts.
-   Never execute arbitrary model-generated shell/Python/JavaScript as a
    normal test action.
-   Every external target must be validated against explicit allowlists
    and SSRF defenses.
-   Every artifact endpoint must enforce project authorization.
-   Do not retry non-idempotent operations blindly.
-   Preserve run/attempt history; do not erase prior failures.
-   Prefer small vertical slices with tests over speculative
    abstraction.

## Engineering workflow

1.  Inspect the repository and existing conventions before editing.
2.  Identify the relevant task in `TASKS.md`; update it only when status
    actually changes.
3.  Write or update acceptance tests before or alongside implementation.
4.  Keep interfaces typed and schemas explicit.
5.  Run focused tests, then relevant integration checks.
6.  Report exactly what changed, commands run, results, known
    limitations, and next task.
7.  If requirements conflict, stop and document the conflict rather than
    inventing product behavior.
8.  Update `DECISIONS.md` when a lasting architecture choice changes.

## Architecture boundaries

-   UI talks to the API, never directly to the database or worker
    internals.
-   API validates authorization and creates durable work; workers
    execute long-running tests.
-   Adapters implement the published contract and cannot define global
    policy.
-   The policy engine remains outside the LLM and adapter.
-   Evidence is referenced through controlled storage APIs; never expose
    raw object-store credentials.
-   Shared contracts are the boundary between packages.

## Code quality

-   Add input validation and explicit error handling.
-   Avoid broad exception swallowing.
-   Use migrations for schema changes.
-   Do not commit secrets, local databases with real data, browser
    profiles, or test artifacts.
-   Add regression tests for bugs.
-   Keep dependencies pinned/locked according to repository tooling.
-   Avoid unnecessary new dependencies; explain why a new one is needed.

## Definition of done

A task is complete when acceptance criteria are met, tests pass,
security/privacy implications are addressed, docs are updated, and
remaining limitations are stated.
