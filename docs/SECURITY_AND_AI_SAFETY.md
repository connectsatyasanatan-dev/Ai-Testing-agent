# Security, Privacy, and AI Safety

## 1. Security objectives

Protect target systems, credentials, test artifacts, customer data, and
the control plane. Assume that prompts, web pages, API responses, files,
logs, and model output can be malicious or misleading.

## 2. Threat model

-   Prompt injection in a web page, issue description, API response, or
    document.
-   Model proposes an unintended destructive action.
-   SSRF or requests to internal/cloud metadata addresses.
-   Credential leakage through logs, screenshots, traces, or model
    context.
-   Cross-project data access.
-   Malicious or compromised adapter.
-   Worker escape or resource exhaustion.
-   Replay/duplicate execution after a crash.
-   Artifact links exposed to unauthorized users.
-   Supply-chain compromise in dependencies or browser binaries.
-   Execution of a run against a stale, unapproved, or revoked plan
    version (approval TOCTOU).
-   Artifact ID enumeration by an authenticated user in a different
    project.

## 3. Authentication and authorization

-   Use a mature authentication library and secure password hashing
    (Argon2id or equivalent).
-   Enforce authorization on every API request and every artifact
    download.
-   Suggested roles: Owner, Maintainer, Viewer; add a dedicated Runner
    role if needed.
-   Use secure, HttpOnly, SameSite cookies for browser sessions;
    protect state-changing requests against CSRF.
-   Issue short-lived opaque API tokens for non-browser clients (CI,
    CLI). Tokens must be revocable and rotatable.
-   Keep session/token lifetime short enough for the deployment's threat
    model; rotate and revoke credentials.
-   Apply rate limits to login, plan generation, and run creation.
    -   **Login Rate Limiting (P0-05):**
        -   *Keying strategy:* Client IP (`ratelimit:login:{ip}`), honoring `X-Forwarded-For` from reverse proxies. Passwords, emails, and credentials are never stored in rate-limit keys or logs.
        -   *Window & limit:* 60-second sliding window log, defaulting to `rate_limit_login = 10` attempts/min (configurable via `UITP_RATE_LIMIT_LOGIN`).
        -   *Distributed backend:* Redis 7 sorted sets (`ZREMRANGEBYSCORE`, `ZCARD`, `ZADD`, `EXPIRE`) executed via atomic Lua script to prevent race conditions across multiple API instances.
        -   *Fail-safe mode:* If Redis is unreachable or unconfigured, the limiter logs an error and degrades gracefully to an in-memory sliding window (fail-open degraded mode).
        -   *Counter policy:* Every attempt increments the counter to mitigate Argon2 CPU exhaustion; successful authentication resets the client IP's counter so legitimate users are not penalized for mistyped credentials.
        -   *Exceeded response:* HTTP `429 Too Many Requests` with `Retry-After: <seconds>` header and error code `RATE_LIMIT_EXCEEDED`.
-   Audit role changes, environment edits, approvals, and execution
    actions. See the audit event taxonomy in ARCHITECTURE.md §5a.
-   **Plan approval gate:** The API must verify that a plan version is
    in `approved` status before creating a run. A run against a `draft`,
    `archived`, or non-existent plan version must be rejected with error
    code `PLAN_NOT_APPROVED`. This check must be atomic; do not rely on
    a pre-check that can race with a concurrent archive or revocation.

## 4. Secrets

-   Store secrets in a dedicated secret manager or encrypted deployment
    secret store.
-   Persist secret references, not plaintext values, in the application
    database.
-   Inject secrets only into the specific worker and run that needs
    them.
-   Never put secrets into prompts, test-plan source, error messages,
    screenshots, or ordinary logs.
-   Redact authorization headers, cookies, API keys, passwords, and
    user-configured patterns.
-   Restrict artifact access; use UUIDv4 artifact IDs and enforce
    project-membership authorization on every retrieval (see §5 below).
    Unguessable IDs are a defence-in-depth measure, not a substitute for
    authorization checks.
-   Rotate credentials after suspected exposure; provide revocation
    workflows.

## 5. Target isolation and SSRF defence

-   Require explicit target registration and allowlisted hosts/ports.
-   Resolve and validate DNS/IP at connection time; block loopback,
    link-local, private RFC-1918, and cloud metadata destinations
    (169.254.169.254, fd00::/8, etc.) unless a trusted admin explicitly
    configures a private test network.
-   Revalidate redirects and DNS resolution to mitigate
    rebinding/redirect bypass.
-   Apply request budgets, rate limits, response-size limits, and
    per-step timeouts.
-   Separate test environments from production; production testing
    requires explicit authorization and conservative policies.

### SSRF controls by deployment tier

| Control                                           | Milestone 1 (local dev) | Milestone 2+ (staging/production) |
|---------------------------------------------------|-------------------------|-----------------------------------|
| Application-layer allowlist and IP validation     | Required (P1-02)        | Required                          |
| Redirect re-validation                            | Required (P1-02)        | Required                          |
| Network-layer egress policy (firewall/iptables)   | **Not enforced**        | **Required**                      |
| Non-root worker container                         | Not enforced            | Required                          |
| Read-only base filesystem                         | Not enforced            | Required                          |
| CPU/memory/PID limits                             | Not enforced            | Required                          |

> **Important:** In Milestone 1, workers run as plain Python processes.
> The application-layer SSRF checks in P1-02 are the only network
> control. They are necessary but not sufficient to prevent a
> compromised adapter or dependency from making out-of-band requests.
> Network-layer egress enforcement is a hard requirement before any
> worker executes tests against shared or production environments.
> Document this gap explicitly in the deployment guide (P6-05).

## 6. Artifact authorization

-   All artifact primary keys must be UUIDv4. Sequential or predictable
    identifiers are prohibited for any resource accessible via the API.
-   Every artifact retrieval request must verify that the artifact's
    owning run belongs to a project the requesting user is a member of.
    This check is in addition to, not instead of, unguessable IDs.
-   Preferred route shape: `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`
    so that the run-level authorization scope is explicit in the URL and
    enforced before the artifact is resolved.
-   Use short-lived signed URLs for object storage downloads where the
    storage backend supports them.
-   Record an `artifact_accessed` audit event for every retrieval.

## 7. AI-specific guardrails

-   Treat retrieved page text, API responses, logs, and user-uploaded
    files as **untrusted data**, not system instructions.
-   Separate trusted policy/configuration from untrusted target content
    in prompts.
-   Ask the model for schema-constrained declarative plans, never
    free-form executable code as the normal execution path.
-   Validate all model output with deterministic schemas, adapter
    capabilities, authorization, target policy, and risk policy.
-   Require human review and explicit approval for every plan before
    execution. The approval step cannot be bypassed even when AI
    generation is used.
-   High-risk actions (deletion, financial transactions, external
    messages, production mutations) are blocked by default or require
    explicit, scoped approval.
-   AI explanations are hypotheses; the raw assertion outcome and
    evidence remain authoritative.
-   Set provider budgets, timeouts, rate limits, and data-handling
    settings.
-   The manual-plan path must work at all times when AI is disabled or
    unavailable. No feature may be unavailable solely because an AI
    provider key is not configured.
-   AI plan generation returns `503 Service Unavailable` with a clear
    message when no provider is configured; it does not block plan
    creation or run execution.

## 8. Worker sandbox

### Milestone 1 (local development — no sandbox)

Workers run inside the API process or as a plain Python subprocess.
There is no container isolation, capability dropping, or network egress
policy. This mode is acceptable only for local development against
non-production targets.

### Milestone 2+ (required for shared/production use)

-   Run workers as separate processes in non-root containers.
-   Read-only base filesystem where practical.
-   CPU, memory, and PID limits applied at the container level.
-   Per-run temporary directories; clean up browser profiles and
    temporary secrets after each run.
-   Drop unnecessary Linux capabilities; do not mount the host Docker
    socket.
-   Restrict network egress at the network layer (not only in
    application code).
-   Avoid sharing browser contexts between tenants or runs.
-   Treat downloads and uploaded files as untrusted; scan or restrict
    them according to deployment needs.

## 9. Evidence and privacy

-   Define retention by artifact type and project policy. The default
    retention period must be resolved before P0-04 (database
    migrations) and recorded in DECISIONS.md.
-   Minimise capture of page content and personal data.
-   Provide configurable masking for screenshots and request/response
    bodies.
-   Record whether redaction succeeded; if it fails, withhold the
    artifact rather than publish it unredacted.
-   Encrypt storage in transit and at rest.
-   Record checksums and metadata for integrity.
-   Ensure deletion/retention workflows include object storage and
    backups according to policy.

## 10. Operational controls

-   Dependency lockfiles and automated vulnerability scanning (pip-audit,
    npm audit) in CI on every main-branch build.
-   Signed or verified build artifacts where feasible.
-   Separate dev, staging, and production credentials.
-   Backups with tested restoration.
-   Alert on repeated policy blocks, anomalous egress, credential-like
    values in logs, and worker crash loops.
-   Incident playbook for secret leakage and unauthorized target
    activity.

## 11. Security acceptance checklist

-   [ ] Tenant/project authorization tests cover API and artifact
    endpoints.
-   [ ] Secrets never appear in normal logs or model prompts.
-   [ ] SSRF tests cover redirects, DNS changes, private IPs, and
    metadata addresses (application-layer; Milestone 1).
-   [ ] Network-layer egress verified in staging before production
    release (Milestone 2).
-   [ ] Untrusted page text cannot change policy or grant tools.
-   [ ] Destructive operations are denied by default.
-   [ ] Workers have resource and network limits (Milestone 2).
-   [ ] Artifacts are redacted before authorized retrieval.
-   [ ] Audit trail records approvals, policy decisions, and artifact
    accesses.
-   [ ] A run cannot be created against a draft, unapproved, archived,
    or non-existent plan version.
-   [ ] Artifact retrieval enforces project-membership authorization
    independent of ID unguessability.
