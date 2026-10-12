# Glossary

-   **Adapter:** target-specific module that executes declared actions
    and emits structured results/evidence.
-   **Capability:** an explicitly declared operation or feature an
    adapter supports.
-   **Control plane:** API and services that manage identity, projects,
    plans, policy, and runs.
-   **Execution worker:** isolated process/container that performs the
    actual test steps.
-   **Evidence:** screenshot, trace, request metadata, response
    metadata, log excerpt, or other artifact supporting a result.
-   **Inconclusive:** not enough trustworthy evidence to determine
    pass/fail.
-   **Plan:** versioned, declarative description of steps, assertions,
    limits, and risk.
-   **Policy engine:** deterministic component that authorizes actions
    based on roles, target rules, and risk.
-   **Prompt injection:** untrusted content attempting to override
    instructions or manipulate the agent.
-   **Run:** one execution of an approved plan against an environment.
-   **Step attempt:** one attempt to execute a particular step,
    including retries.
-   **SSRF:** server-side request forgery; causing a server/worker to
    contact an unintended network destination.
-   **Target allowlist:** explicit list of permitted hosts, addresses,
    ports, or resource scopes.
-   **Conformance test:** common test suite that every adapter must pass
    before it is enabled.
