# Worker Isolation and Safe Execution Architecture

## 1. Executive Summary

This document specifies the safe isolation architecture for test execution workers in the **Universal Autonomous AI Testing Platform**. Before adding browser automation (Playwright) or third-party adapters interacting with untrusted targets, workers must operate inside strict defense-in-depth isolation boundaries.

The core principle: **Never trust target responses, redirect locations, DNS resolutions, or adapter execution environments.**

---

## 2. Threat Model

| Threat | Attack Vector | Impact | Mitigation in UITP |
|---|---|---|---|
| **Server-Side Request Forgery (SSRF)** | Target URL points to `169.254.169.254`, `127.0.0.1`, `10.0.0.0/8`, or cloud metadata endpoints. | Exfiltration of cloud instance credentials or compromise of internal infrastructure. | `SSRFValidator` checks all schemes, IPs, and hostnames. Pre-flight DNS validation rejects any private/reserved/metadata IP. |
| **DNS Rebinding (TOCTOU)** | Domain resolves to a public IP during pre-check, but returns `169.254.169.254` or `127.0.0.1` when HTTP socket connects. | Bypass of application-layer URL validation. | `RebindingSafeTransport` pins outgoing TCP/TLS connections directly to the pre-validated safe IP address, preserving `Host` header and SNI. |
| **Redirect Evasion** | Benign initial URL returns a `302 Found` to an internal service or metadata IP. | Circumvention of target allowlists. | `RedirectValidator` intercepts every redirect hop, enforces max redirect depth, and runs full SSRF validation before proceeding. |
| **Resource Exhaustion (Denial of Service)** | Malicious target sends infinite response streams or slowloris timeouts. | Worker process lockup or out-of-memory crash. | Bounded wall-clock timeouts (`300s`), step timeouts (`30s`), response size caps (`5MB`), and `tmpfs` disk caps (`128MB`). |
| **Evidence Disk Flooding** | Run generates hundreds of megabytes of response logs or screenshots. | Shared disk space exhaustion. | `RunEvidenceBudget` bounds per-run total evidence to 50MB and max 100 artifacts. Excess writes are safely rejected. |
| **Temporary Data & Profile Leakage** | Worker crashes or times out, leaving sensitive scratch files or browser profiles on disk. | Cross-run data leakage or disk leaks. | `ExecutionWorkspace` allocates an isolated temporary directory per run with guaranteed teardown in `finally` blocks. |
| **Container Escape / Privilege Escalation** | Compromised worker exploits container kernel vulnerabilities. | Host machine takeover. | Worker runs as non-root (`UID 10001`), drops all Linux capabilities (`cap_drop: ALL`), enforces `no-new-privileges:true`, and never mounts Docker socket. |
| **Credential & Signing Key Theft** | Worker environment leaks control-plane session secrets. | Control-plane takeover. | `UITP_SECRET_KEY` is completely excluded from worker container environments. Environment secrets are indirect references (`secret_refs`), never raw values. |

---

## 3. Multi-Layer Isolation Boundaries

```
+-------------------------------------------------------------------------------+
| Layer 1: Control Plane Boundary (FastAPI API)                                 |
| - Authorization checks & Exact-version Plan Approval Gate                     |
| - Never executes test steps; enqueues UUIDv4 to Redis/RQ queue only           |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| Layer 2: Container & Process Sandbox                                          |
| - Non-root execution: UID 10001 (uitp)                                        |
| - Dropped Linux capabilities: cap_drop: [ALL]                                 |
| - Security flags: no-new-privileges:true, no host socket mounts               |
| - Resource quotas: 1.0 CPU, 1024MB RAM, 100 PIDs                              |
| - Bounded tmpfs: /tmp (128MB, noexec, nosuid, nodev)                          |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| Layer 3: Worker Runner Lifecycle & Budgets (uitp_worker_runner)               |
| - Wall-Clock Timeout: Monotonic deadline enforcement (default 300s)           |
| - External Cancellation: Proactive status check between steps                 |
| - Isolated Workspace: Dedicated per-run scratch dir (ExecutionWorkspace)      |
| - Evidence Quota: Hard ceiling of 50MB / 100 artifacts (RunEvidenceBudget)    |
+-------------------------------------------------------------------------------+
                                      |
                                      v
+-------------------------------------------------------------------------------+
| Layer 4: Execution Policy & Target Boundaries (uitp_policy)                   |
| - Centralized ExecutionPolicy (step timeout <= 30s, payload <= 5MB)           |
| - Allowed URL Schemes: http, https only (file://, gopher:// rejected)         |
| - SSRFValidator: Prohibits RFC1918, loopback, link-local, cloud metadata      |
| - RebindingSafeTransport: Pins socket connections to validated safe IP        |
| - RedirectValidator: Re-evaluates all redirect hops & strips sensitive auth   |
+-------------------------------------------------------------------------------+
```

---

## 4. Execution Limits Configuration Reference

All execution parameters are defined centrally in `ExecutionLimits`:

| Parameter | Default | Range / Bounds | Purpose |
|---|---|---|---|
| `wall_clock_timeout_seconds` | `300.0` (5m) | `1.0` – `1800.0` (30m) | Upper limit for an entire test run across all steps and retries. |
| `step_timeout_seconds` | `30.0` | `0.5` – `120.0` | Maximum duration allowed for a single step attempt. |
| `max_request_bytes` | `5,242,880` (5MB) | `> 0` | Maximum request payload body size permitted. |
| `max_response_bytes` | `5,242,880` (5MB) | `> 0` | Maximum response body or Content-Length accepted. |
| `max_redirects` | `5` | `0` – `10` | Maximum HTTP redirect hops before terminating attempt. |
| `max_attempts_per_step` | `3` | `1` – `5` | Upper ceiling on retries (non-idempotent steps capped at 1). |
| `max_total_evidence_bytes_per_run` | `52,428,800` (50MB) | `> 0` | Hard disk quota for all artifacts written during one run. |
| `max_artifacts_per_run` | `100` | `> 0` | Maximum number of artifact files stored per run. |
| `max_temp_dir_bytes` | `104,857,600` (100MB) | `> 0` | Temporary scratch storage limit per run. |
| `max_concurrency` | `4` | `> 0` | Max simultaneous test runs per worker instance. |
| `allowed_schemes` | `("http", "https")` | Fixed tuple | Blocks dangerous pseudo-schemes (`file`, `gopher`, `dict`). |

---

## 5. Supported Deployment Modes

### Mode A: Local Prototype (Milestone 1)
- Workers execute in background thread or local process.
- SQLite in-memory database, local filesystem storage (`./artifacts`).
- Application-layer SSRF validation, DNS validation, and `ExecutionWorkspace` cleanup active.
- *Limitation:* No kernel cgroups or OS network namespace isolation. Use only against non-production targets.

### Mode B: Containerized Worker (Milestone 2)
- Workers execute as standalone containers via Docker Compose.
- Redis Queue transports jobs (`uitp_runs`).
- Non-root user (`UID 10001`), `no-new-privileges:true`, `cap_drop: ALL`, bounded `tmpfs`, and CPU/memory limits.
- Secret keys (`UITP_SECRET_KEY`) strictly excluded from worker containers.

### Mode C: Production Sandbox (Milestone 2+)
- Dedicated worker nodes or microVMs (Firecracker / gVisor).
- Network egress gateway / iptables blocking private RFC1918 and cloud metadata destinations at the packet filter level.

---

## 6. Known Limitations & Remaining Requirements

1. **Docker Compose Network Filtering:**
   Docker Compose bridge networks allow container-to-container communication by default unless custom Docker networks or host firewall (iptables) rules restrict outbound traffic. Full network-layer egress enforcement is an infrastructure requirement for staging/production deployments.
2. **Browser Automation Prerequisite:**
   Playwright browser automation (Milestone 2) must launch browsers inside `ExecutionWorkspace` with `--user-data-dir` set to a workspace subdirectory and an outbound proxy enforcing `RebindingSafeTransport` before untrusted web navigation is enabled.
