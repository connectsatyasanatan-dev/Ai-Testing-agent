# Adapter Architecture and Capability Matrix

## 1. Purpose

Adapters make the platform extensible across target types without
pretending that all targets have the same semantics. The orchestrator
speaks a small common contract; adapters implement target-specific
behavior and declare capabilities.

## 2. Adapter contract

Each adapter should implement equivalent operations for: - `metadata()`
--- stable adapter id, version, supported action types, limitations. -
`validate_target()` --- safe connectivity and configuration
validation. - `validate_step()` --- schema, action, timeout, and
capability validation. - `execute_step(context, step)` --- execute one
bounded step and return structured outcome. - `health_check()` ---
worker dependency readiness. - `cancel(run_context)` --- best-effort
cancellation. - `collect_evidence()` --- emit references/metadata for
logs, screenshots, traces, or responses. - `sanitize()` --- redact
sensitive values before evidence leaves the worker. -
`conformance_tests()` --- common behavioral suite.

An adapter must not silently ignore an action. It returns a typed error
such as `UNSUPPORTED_ACTION`, `TARGET_UNREACHABLE`,
`AUTHENTICATION_FAILED`, `ASSERTION_FAILED`, `TIMEOUT`, or
`POLICY_BLOCKED`.

## 3. Shared step envelope

A normalized step should contain: - `step_id` - `adapter_type` -
`action` - `inputs` (validated against action schema) - `assertions` -
`timeout_seconds` - `retry_policy` - `risk_level` - `evidence_policy`

Never allow model output to supply executable Python, JavaScript, shell,
or arbitrary code as a default action. Prefer allowlisted declarative
actions.

## 4. Initial capability matrix

  ----------------------------------------------------------------------------------------
  Target type       MVP status        Example capabilities        Main constraints
  ----------------- ----------------- --------------------------- ------------------------
  Web browser       **MVP target**    Navigate, click, fill,      Playwright-supported
                                      select, wait, assert        browsers; allowlisted
                                      text/visibility/URL,        domains;
                                      screenshot, trace           downloads/uploads need
                                                                  policy

  HTTP API          **MVP target**    GET/POST/PUT/PATCH/DELETE   Domain allowlist, secret
                                      when allowed, headers, JSON redaction, rate limits,
                                      body,                       safe-method policy
                                      status/schema/JSONPath      
                                      assertions                  

  Mobile app        Planned           Launch, tap, type, assert   Requires device/emulator
                                      UI state, screenshots       farm and
                                                                  platform-specific
                                                                  tooling

  Desktop app       Planned           Window/UI actions,          OS-specific runners;
                                      screenshots, assertions     accessibility tree
                                                                  support varies

  CLI               Planned /         Run predefined commands and Allowlisted
                    restricted        inspect output/exit code    executables/arguments;
                                                                  no arbitrary shell by
                                                                  default

  Event-driven      Planned           Publish/consume messages,   Requires broker-specific
  systems                             assert event schemas and    adapter and isolated
                                      timing                      test topics

  Database          Planned /         Query approved views,       Parameterized queries,
                    read-only first   assert rows/counts          read-only credentials,
                                                                  query allowlist

  Visual regression Planned           Compare screenshots against Threshold tuning,
                    capability        approved baselines          fonts/rendering drift,
                                                                  baseline governance

  Accessibility     Planned           Automated rule checks on    Automated checks do not
                    capability        web pages                   replace manual
                                                                  accessibility testing
  ----------------------------------------------------------------------------------------

MVP support means only the capabilities that have implementation and
passing conformance tests are enabled. A row labeled planned is not
available merely because the architecture anticipates it.

## 5. Capability negotiation and Adapter Registry (P1-01)

The platform enforces strict capability negotiation before any step execution:

1. **Base Adapter Contract (`uitp_adapter_base`):**
   - Implemented via `BaseAdapter(ABC)`.
   - `metadata()` declares versioned `AdapterCapabilities` (`adapter_id`, `adapter_type`, `version`, `supported_actions`, `limitations`).
   - `supports_action(action: str) -> bool` and `validate_step(step: TestStepSchema)` provide early validation.
   - Unsupported actions raise `UnsupportedActionError` carrying `code: ErrorCode.UNSUPPORTED_ACTION`.
   - Step execution is async via `execute_step(step, context) -> StepExecutionResult`.

2. **Adapter Registry (`AdapterRegistry`):**
   - In-memory registry for managing adapters and validating steps against capabilities.
   - Enforces unique `adapter_id` keys; duplicate registration is rejected with `DuplicateAdapterError`.
   - Provides deterministic retrieval and enumeration without import-time side effects.
   - Never instantiates arbitrary classes from user strings or dynamic model code.

3. **Current Adapter Status:**
   - **HTTP API (`uitp_adapter_http_api`):** Capabilities declared (`http_api_v1`, actions: `http_request`, `get`, `post`, `put`, `patch`, `delete`, `head`, `options`). Step execution implemented in task P1-04.
   - **Web / Playwright (`uitp_adapter_web`):** Implemented and registered (`web_playwright_v1`, actions: `navigate`, `goto`, `click`, `fill`, `select`, `select_option`, `wait_for`, `wait`, `screenshot`). Enforces route-level SSRF defense, bounded timeouts and page limits, deterministic assertions, and evidence storage (Tasks P2-01 through P2-05).

Before running a plan:
1. Resolve adapter from registry by `adapter_id` or `adapter_type`.
2. Validate every plan step against the adapter's declared capabilities.
3. Validate target environment policy and SSRF allowlists.
4. Reject the plan or mark steps blocked if requirements cannot be met.
5. Persist the capability document/version with the run for reproducibility.

## 6. Adapter conformance suite

Every adapter must prove: - Metadata is stable and machine-readable. -
Invalid inputs fail closed. - Unsupported actions return a typed
error. - Timeouts terminate work within a bounded grace period. -
Cancellation is honored where supported. - Evidence is linked to the
correct attempt. - Secrets are redacted in logs and artifact metadata. -
No action occurs outside the configured target/command allowlist. -
Re-running safe/idempotent steps behaves predictably. - Failures produce
actionable error codes and do not become false passes.

## 7. Plugin governance

Initially, adapters should be first-party packages loaded from a trusted
registry/configuration. Do not dynamically install arbitrary plugins
from model output. A future third-party adapter ecosystem needs signing,
version pinning, permissions, sandboxing, compatibility checks, and
revocation.

## 8. Secure HTTP API Testing Adapter (P1-04)

The HTTP API adapter (`uitp_adapter_http_api`) implements real, bounded HTTP step execution:

1. **Supported Capabilities:**
   - Actions: `http_request`, `get`, `post`, `put`, `patch`, `delete`, `head`, `options`.
   - Supported methods: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`.
   - Body formats: JSON (`json`) and binary/text payloads (`body` / `data`).

2. **Security & Request Construction:**
   - Relative paths are joined against `ExecutionContext.base_url`.
   - Control-character checks reject CRLF injection in URLs and headers.
   - Pre-request SSRF validation via `uitp_policy.SSRFValidator` against private IPs, loopback, link-local, and cloud metadata (169.254.169.254).
   - Target allowlist matching against project/environment rules.

3. **Redirect & Cross-Origin Enforcement:**
   - HTTPX automatic redirect following is disabled (`follow_redirects=False`).
   - Manual redirect loop validates every intermediate target against SSRF and allowlists using `RedirectValidator`.
   - Redirect limit is strictly bounded (default: 5 hops; loops rejected).
   - Sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`) are stripped on cross-origin redirects.

4. **Idempotency & Retry Rules:**
   - `idempotency_class` is authoritative; HTTP method alone does not determine retry eligibility.
   - Non-idempotent (`non_idempotent`, `unknown`) steps are attempted **exactly once**. Any `retry_policy` is bypassed and a warning is logged.
   - Idempotent (`safe`, `idempotent`) steps retry transport errors or configured `retryable_status_codes` up to `max_attempts`.
   - Policy-blocked steps (`POLICY_BLOCKED`, `TARGET_NOT_ALLOWLISTED`) are terminal and never retried.

5. **Resource Limits & Output Representation:**
   - Response bodies are capped (default: 5 MB).
   - Execution time is bounded by `step.timeout_seconds` and `context.timeout_seconds`.
   - Outputs capture status code, allowlisted headers, parsed JSON or raw text, duration, attempt count, and redirect count.
   - Passwords and raw tokens are excluded from logs.

## 9. Deterministic HTTP Assertion Engine (P1-05)

The HTTP assertion engine (`uitp_adapter_http_api.assertions.HttpAssertionEngine`) evaluates declarative assertions against structured response outputs:

1. **Supported Assertion Types:**
   - `status_code`: Exact integer HTTP status match (range 100-599).
   - `json_path_equals`: Scalar/object value comparison at a target JSONPath.
   - `json_path_exists`: Verification that target JSON path exists (including null/empty values).
   - `json_path_matches`: Regex matching against scalar value at target JSONPath.
   - `header_equals`: Case-insensitive HTTP response header comparison.
   - `header_exists`: Case-insensitive check that a header is present.
   - `json_schema`: Schema validation using JSON Schema Draft 7 / 2020-12 core vocabulary.
   - `response_time_under`: Compares execution elapsed duration (`duration_ms`) against millisecond threshold.

2. **JSONPath Syntax & Traversal Bounds:**
   - Syntax: Dot-notation and array indices (e.g., `$.items[0].id`, `user.profile.name`).
   - Bounds: Max path length 256 characters, max traversal depth 20 levels.
   - Safety: Disallows recursive descent (`..`), wildcards (`*`), filter expressions (`?()`), and dynamic scripts. Zero use of `eval` or `exec`.

3. **JSON Schema Capabilities:**
   - Supported keywords: `type`, `properties`, `required`, `additionalProperties`, `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`.
   - Bounds: Max schema recursion depth 15 levels, max 100 properties.
   - Concise mismatch paths (`#/user/id`) without dumping raw response bodies.

4. **Security & Redaction:**
   - Sensitive headers (`Set-Cookie`, `Cookie`, `Authorization`, `Proxy-Authorization`, `X-Api-Key`) are redacted (`<redacted>`) in error messages.
   - Web assertions (`url_contains`, `text_visible`, `element_visible`, `element_hidden`) return typed `ErrorCode.UNSUPPORTED_ASSERTION`.

5. **Aggregation & Determinism:**
   - Evaluates all assertions in declared order to provide complete diagnostic telemetry.
   - Returns typed `StepAssertionsOutcome` (`passed`, `total_assertions`, `passed_count`, `failed_count`, `results`, `failure_reasons`).
   - When any assertion fails, step status becomes `FAILED` with error code `ASSERTION_FAILED`.

## 10. Evidence Storage and Project-Authorized Artifact Retrieval (P1-06B)

The evidence subsystem (`uitp_evidence`) and artifact APIs provide bounded, safe, and project-authorized diagnostic telemetry storage:

1. **Evidence Storage Abstraction:**
   - Interface: `EvidenceStorage` base class with concrete `LocalFilesystemEvidenceStorage`.
   - Security: Operates strictly beneath a configured, application-controlled base directory (`UITP_ARTIFACT_STORAGE_PATH`).
   - Defenses: Path traversal tokens (`..`), absolute paths, symlink escapes, and null bytes are rejected with `StorageSecurityError`.
   - Atomic Writes: Writes to a transient `.tmp.<uuid>` file on the same filesystem, flushes, syncs (`os.fsync`), and atomically replaces (`os.replace`).
   - Integrity: Computes and validates SHA-256 digests on write and read (`CorruptedArtifactError` raised on tampering).
   - Opaque Storage Keys: Internal object references (e.g., `runs/{run_id}/{artifact_id}.bin`) are decoupled from public UUIDv4 IDs and never leaked to API callers.

2. **Artifact Types & Limits:**
   - Types: `screenshot`, `trace`, `request_log`, `response_log`, `console_log`, `har`.
   - Size Limits: Configurable bounded limit (`UITP_ARTIFACT_MAX_SIZE_BYTES`, default 10MB). Payloads exceeding the limit are rejected before writing (`PayloadSizeLimitExceededError`).

3. **Sensitive-Data Redaction Engine (`RedactionEngine`):**
   - Headers: Masks `Authorization`, `Cookie`, `Set-Cookie`, `Proxy-Authorization`, `X-API-Key`, and case-insensitive matches.
   - URLs: Masks basic authentication userinfo (`user:password`) and credentials in query parameters (`token`, `api_key`, `key`, `secret`, `signature`).
   - Payloads: Deep recursive sanitization of nested dictionaries and lists for sensitive keys (`password`, `secret_key`, `token`, `session_id`, etc.).
   - Regex Scanning: Masks Bearer tokens (`Bearer [REDACTED]`) and JWT strings (`[REDACTED]`) in unstructured text.
   - Bounded Lengths: Truncates oversized string representations with an explicit `... [TRUNCATED X bytes]` marker.

4. **Retention Lifecycle (ADR-013):**
   - Retention Period: Defaults to 30 days (`UITP_ARTIFACT_RETENTION_DAYS`), persisted on the `retention_until` column of `EvidenceArtifact`.
   - History Preservation: Payload expiry or pruning preserves `Run`, `StepAttempt`, `RunEvent`, and artifact metadata rows. Expired artifact retrieval returns 404 without altering prior run outcomes.

5. **Authorized Retrieval Endpoints:**
   - `GET /api/v1/runs/{run_id}/artifacts`: Lists metadata for unexpired artifacts associated with the run.
   - `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`: Returns metadata + base64 inline content, or raw binary stream when `?download=true` or `Accept: application/octet-stream`.
   - Tenant Isolation: Scoped strictly to project membership (`owner`, `maintainer`, `viewer`). Requests from non-members or cross-project users receive 404 to prevent resource enumeration.
   - Audit Trail: Emits `ARTIFACT_ACCESSED` audit event without recording payloads or credentials.

## 11. Playwright Web Browser Testing Adapter (P2-01 through P2-05)

The Web adapter (`uitp_adapter_web`) provides deterministic, browser-based UI automation using Playwright Chromium:

1. **Declared Capabilities:**
   - Adapter ID: `web_playwright_v1`
   - Adapter Type: `web` (AdapterType.WEB)
   - Version: `0.1.0`
   - Actions: `navigate` (alias `goto`), `click`, `fill`, `select` (alias `select_option`), `wait_for` (alias `wait`), `screenshot`.

2. **Security Controls & Sandboxing:**
   - **Network-Level SSRF Defense (`BrowserSecurityRouter`):** Intercepts 100% of browser traffic via `context.route("**/*")`. Enforces strict scheme allowlist (`http`, `https`), revalidates all redirects via `route.fetch(max_redirects=0)`, and blocks prohibited targets (RFC-1918, link-local, AWS/GCP/Azure metadata `169.254.169.254`, IPv6 equivalents, and non-allowlisted domains). Blocked requests are immediately aborted with `blockedbyclient`.
   - **Prohibition of Arbitrary Code:** Prohibits `eval`, arbitrary JavaScript strings, `javascript:` URLs, function expressions, DOM object traversal expressions (`window.`, `document.`, `__proto__`), browser extensions, and shell commands.
   - **Strict Locator Validation (`LocatorResolver`):** Supports only declared declarative strategies: `css`, `text`, `test_id` (`data-testid`), `role`, `label`, and `placeholder`. Rejects malformed or oversized selectors.
   - **Resource Boundaries:** Bounded navigation timeouts (default 15s) and action timeouts (default 5s). Caps concurrent browser pages/popups at 3 (excess popups closed immediately). Downloads cancelled by default. Ephemeral browser contexts destroyed on completion.
   - **Credential Redaction:** Masks password and secret values in step execution logs (`[REDACTED]`).

3. **Deterministic Web Assertions (`WebAssertionEngine`):**
   - Assertions: `url_contains`, `url_equals`, `title_contains`, `title_equals`, `text_visible`, `element_visible`, `element_hidden`.
   - Bounded execution timeouts (default 3000ms).
   - Generates structured, deterministic `StepAssertionsOutcome`. Assertion failure automatically triggers failure screenshot evidence capture.

4. **Evidence & Artifact Capture:**
   - Full-page or viewport screenshots captured on `screenshot` action and on step assertion failures.
   - Verified with SHA-256 integrity checksums and stored via `uitp_evidence` under `artifact_type="screenshot"` (`image/png`).


