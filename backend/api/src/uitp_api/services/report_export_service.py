"""
uitp_api.services.report_export_service
=======================================
Evidence-based execution log and report export service.

Responsibilities:
  - Generates versioned, validated execution_log.json from persisted DB records.
  - Generates human-readable report.md with requirement traceability, task outcomes,
    grounded root causes, and failure screenshot references.
  - Copies evidence artifacts (screenshots, response logs) to collision-safe directories.
  - Ensures atomic file writes (.tmp.<uuid> -> os.replace) so partial/interrupted runs
    never leave corrupted files.
  - Enforces secret redaction on all exported inputs, outputs, and telemetry.
  - Preserves per-run isolation (output/runs/<run_id>/) while mapping active run to
    assignment deliverable paths (output/execution_log.json and output/report.md).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.config import get_settings
from uitp_api.exceptions import ExportError, ResourceNotFoundError
from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt, RunContext
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.services.evidence_service import get_evidence_storage
from uitp_contracts.export import (
    AssertionResultSummary,
    ExecutionLogSchema,
    ExportResult,
    RequirementTraceabilityItem,
    StepExecutionLog,
    TaskEvidenceRef,
    TaskExecutionLog,
)
from uitp_evidence.redaction import default_redaction_engine
from uitp_evidence.storage import EvidenceStorage
from uitp_orchestrator.context import redact_sensitive_values

logger = logging.getLogger(__name__)


def _atomic_write_file(target_path: Path, content: str | bytes) -> None:
    """Write content to target_path atomically using a temporary file in the same directory."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.parent / f"{target_path.name}.tmp.{uuid.uuid4().hex}"
    mode = "wb" if isinstance(content, bytes) else "w"
    encoding = None if isinstance(content, bytes) else "utf-8"

    try:
        with open(temp_path, mode, encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target_path)
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise ExportError(f"Failed to atomically write {target_path}: {exc}") from exc


def _sanitize_data(data: Any, secret_values: set[str] | None = None) -> Any:
    """Deeply sanitize dictionaries/lists and scrub secrets and sensitive keys."""
    if not data:
        return data

    # 1. Redact known context secrets
    scrubbed = redact_sensitive_values(data, secret_values or set())

    # 2. Redact standard auth headers / tokens if in dict
    if isinstance(scrubbed, dict):
        sanitized_dict = {}
        for k, v in scrubbed.items():
            lower_k = str(k).lower()
            if any(s in lower_k for s in ("auth", "password", "token", "secret", "cookie")):
                sanitized_dict[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                sanitized_dict[k] = _sanitize_data(v, secret_values)
            else:
                sanitized_dict[k] = v
        return sanitized_dict
    elif isinstance(scrubbed, list):
        return [_sanitize_data(item, secret_values) for item in scrubbed]
    return scrubbed


def _determine_error_category(
    task_status: str,
    error_message: str | None,
    step_logs: list[StepExecutionLog],
    events: list[RunEvent],
) -> str | None:
    """Categorize failure deterministically from assertions, exceptions, and events."""
    if task_status == "passed":
        return None
    if task_status == "blocked":
        return "upstream_blocked"

    err_text = (error_message or "").lower()

    # Check for cancellation
    if any(e.event_type == "run_cancelled" for e in events) or "cancel" in err_text:
        return "cancellation"

    # Check for timeout
    if any(e.event_type == "run_timeout" for e in events) or "timeout" in err_text or "timed out" in err_text:
        return "timeout"

    # Check for context/export error
    if "context" in err_text or "export" in err_text:
        return "context_error"

    # Check for policy/SSRF blocked
    if any("policy_blocked" in e.event_type for e in events) or "ssrf" in err_text or "allowlist" in err_text:
        return "policy_blocked"

    # Check for assertion or schema failures in steps
    for s in step_logs:
        for a in s.assertions:
            if a.status == "failed":
                if "schema" in a.assertion_type.lower():
                    return "schema_validation_failure"
                return "assertion_failure"

    if "assertion" in err_text or "expected" in err_text:
        return "assertion_failure"
    if "schema" in err_text:
        return "schema_validation_failure"

    if not error_message and not any(s.error_message for s in step_logs):
        return "insufficient_evidence"

    return "runtime_error"


def _generate_markdown_report(
    log_data: ExecutionLogSchema,
    plan_name: str,
    environment_name: str | None,
) -> str:
    """Synthesize human-readable Markdown report grounded in persisted telemetry."""
    status_badge = "PASSED" if log_data.status == "passed" else log_data.status.upper()
    dur_str = f"{log_data.duration_seconds:.2f}s" if log_data.duration_seconds is not None else "N/A"
    start_str = log_data.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if log_data.started_at else "N/A"
    finish_str = log_data.finished_at.strftime("%Y-%m-%d %H:%M:%S UTC") if log_data.finished_at else "In Progress"

    lines: list[str] = [
        f"# Test Execution Report: {plan_name}",
        "",
        "## 1. Executive Summary",
        "",
        f"- **Run ID:** `{log_data.run_id}`",
        f"- **Mission / Plan ID:** `{log_data.mission_id}` (v{log_data.plan_version})",
        f"- **Target Environment:** `{environment_name or log_data.environment_id}`",
        f"- **Test Suite:** `{getattr(log_data, 'suite_type', 'all')}`",
        f"- **Overall Verdict:** **`{status_badge}`**",
        f"- **Started At:** {start_str}",
        f"- **Finished At:** {finish_str}",
        f"- **Total Duration:** {dur_str}",
    ]

    eval_meta = log_data.metadata.get("evaluation") if log_data.metadata else None
    if eval_meta and isinstance(eval_meta, dict):
        exec_sum = eval_meta.get("executive_summary")
        if exec_sum:
            lines.extend(["", f"> **Evaluator Assessment:** {exec_sum}"])

    lines.extend([
        "",
        "### Results Overview",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Total Tasks | {log_data.summary.get('total_tasks', 0)} |",
        f"| Passed Tasks | {log_data.summary.get('passed_tasks', 0)} |",
        f"| Failed Tasks | {log_data.summary.get('failed_tasks', 0)} |",
        f"| Blocked Tasks | {log_data.summary.get('blocked_tasks', 0)} |",
        f"| Skipped / Incomplete | {log_data.summary.get('skipped_tasks', 0)} |",
        "",
        "---",
        "",
        "## 2. Requirement Traceability Matrix",
        "",
    ])

    if log_data.requirement_traceability:
        lines.extend([
            "| Requirement ID | Status | Tested by Tasks | Findings & Notes |",
            "|---|---|---|---|",
        ])
        for req in log_data.requirement_traceability:
            tasks_str = ", ".join(f"`{t}`" for t in req.tested_by_tasks) if req.tested_by_tasks else "None"
            notes_str = req.notes or "Validated against acceptance criteria."
            lines.append(f"| `{req.requirement_id}` | **{req.status}** | {tasks_str} | {notes_str} |")
    else:
        lines.append("*No explicit requirement IDs were mapped to tasks in this execution plan.*")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Task & Step Execution Results",
        "",
    ])

    for task in log_data.tasks:
        task_status = task.status.upper()
        task_dur = f"{task.duration_ms / 1000:.2f}s" if task.duration_ms else "N/A"
        deps_str = ", ".join(f"`{d}`" for d in task.depends_on) if task.depends_on else "None"
        reqs_str = ", ".join(f"`{r}`" for r in task.requirement_refs) if task.requirement_refs else "None"
        pri_str = task.priority or "P1"

        lines.extend([
            f"### Task: `{task.task_id}` — {task.name}",
            f"- **Role:** `{task.role}` | **Adapter:** `{task.adapter_type}` | **Priority:** `{pri_str}` | **Status:** **`{task_status}`** | **Duration:** {task_dur}",
            f"- **Dependencies:** {deps_str} | **Requirements:** {reqs_str}",
        ])

        if task.steps:
            lines.extend([
                "",
                "| Step ID | Action | Status | Duration (ms) | Assertions | Details |",
                "|---|---|---|---|---|---|",
            ])
            for step in task.steps:
                step_dur = f"{step.duration_ms:.1f}" if step.duration_ms is not None else "-"
                step_st = step.status.upper()
                pass_ass = sum(1 for a in step.assertions if a.status == "passed")
                total_ass = len(step.assertions)
                ass_str = f"{pass_ass}/{total_ass} passed" if total_ass > 0 else "-"

                details: list[str] = []
                if step.http_status is not None:
                    details.append(f"HTTP {step.http_status}")
                if step.ui_action_outcome:
                    act = step.ui_action_outcome.get("action", "")
                    loc = step.ui_action_outcome.get("locator", "")
                    details.append(f"{act} ({loc})")
                if step.error_message:
                    details.append(f"Error: {step.error_message}")
                detail_str = "; ".join(details) or "-"

                lines.append(f"| `{step.step_id}` | `{step.action}` | **{step_st}** | {step_dur} | {ass_str} | {detail_str} |")
        else:
            lines.append("- *No discrete steps recorded (e.g. task blocked before execution).*")

        lines.append("")

    lines.extend([
        "---",
        "",
        "## 4. Root-Cause Diagnostics & Failure Evidence",
        "",
    ])

    failed_or_blocked = [t for t in log_data.tasks if t.status in ("failed", "blocked")]
    if failed_or_blocked:
        for t in failed_or_blocked:
            err_cat = t.error_category or "unknown"
            diag_text = t.diagnostic or "[INSUFFICIENT_EVIDENCE] Root cause cannot be conclusively verified from telemetry."
            lines.extend([
                f"### Diagnostic for `{t.task_id}` ({t.status.upper()})",
                f"- **Failure Classification:** `{err_cat}`",
                f"- **Root Cause:** {diag_text}",
            ])

            if t.evidence_references:
                lines.append("- **Captured Evidence:**")
                for ev in t.evidence_references:
                    rel_p = ev.relative_path or f"evidence/{ev.artifact_id}.bin"
                    if ev.type == "screenshot":
                        lines.append(f"  - Failure Screenshot: ![{t.task_id} Screenshot]({rel_p})")
                    else:
                        lines.append(f"  - `{ev.type}`: [{ev.artifact_id}]({rel_p})")
            lines.append("")
    else:
        lines.append("All tasks passed successfully without errors or blocked prerequisites.")

    lines.extend([
        "",
        "---",
        "",
        "## 5. Execution Timeline & Retry History",
        "",
    ])

    if log_data.retry_history:
        lines.extend([
            "### Retries Scheduled",
            "| Step ID | Attempt | Next Attempt | Delay (s) | Reason |",
            "|---|---|---|---|---|",
        ])
        for r in log_data.retry_history:
            lines.append(f"| `{r.get('step_id')}` | {r.get('attempt_number')} | {r.get('next_attempt')} | {r.get('delay_seconds')} | {r.get('message')} |")
        lines.append("")

    if log_data.timeline_events:
        lines.extend([
            "### Event Log",
            "| Timestamp | Event Type | Message |",
            "|---|---|---|",
        ])
        for ev in log_data.timeline_events[:50]:  # Cap at 50 events for readability
            ts = ev.get("timestamp", "")
            lines.append(f"| {ts} | `{ev.get('event_type')}` | {ev.get('message', '')} |")
        if len(log_data.timeline_events) > 50:
            lines.append(f"| ... | *(+{len(log_data.timeline_events) - 50} more events in execution_log.json)* | |")

    return "\n".join(lines)


async def export_run_execution_artifacts(
    session: AsyncSession,
    *,
    run_id: str,
    output_dir: str | Path | None = None,
    storage: EvidenceStorage | None = None,
    export_root_deliverables: bool = True,
) -> ExportResult:
    """Generate and persist execution_log.json and report.md from real execution records."""
    settings = get_settings()
    base_out_dir = Path(output_dir or settings.export_output_dir).resolve()
    active_storage = storage or get_evidence_storage()

    # 1. Fetch Run
    run = await session.get(Run, run_id)
    if run is None:
        raise ResourceNotFoundError(f"Run '{run_id}' not found.")

    # 2. Fetch Plan
    plan_query = select(TestPlan).where(or_(TestPlan.id == run.plan_id, TestPlan.plan_id == run.plan_id))
    plans = (await session.execute(plan_query)).scalars().all()
    plan = next((p for p in plans if p.version == run.plan_version), plans[0] if plans else None)
    plan_name = plan.name if plan else f"Plan {run.plan_id}"

    # 3. Fetch Tasks & Dependencies
    plan_tasks_query = select(DAGTask).where(or_(DAGTask.plan_id == run.plan_id, DAGTask.plan_id == getattr(plan, "id", run.plan_id)))
    plan_tasks = (await session.execute(plan_tasks_query)).scalars().all()

    deps_query = select(DAGDependency).where(or_(DAGDependency.plan_id == run.plan_id, DAGDependency.plan_id == getattr(plan, "id", run.plan_id)))
    deps = (await session.execute(deps_query)).scalars().all()
    task_deps_map: dict[str, list[str]] = {}
    for d in deps:
        task_deps_map.setdefault(d.child_task_id, []).append(d.parent_task_id)

    # 4. Fetch Attempts, Events, Artifacts, and Contexts
    task_attempts_query = select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id).order_by(DAGTaskAttempt.attempt_number)
    task_attempts = (await session.execute(task_attempts_query)).scalars().all()

    step_attempts_query = select(StepAttempt).where(StepAttempt.run_id == run_id).order_by(StepAttempt.started_at, StepAttempt.attempt_number)
    step_attempts = (await session.execute(step_attempts_query)).scalars().all()

    events_query = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.timestamp)
    events = (await session.execute(events_query)).scalars().all()

    artifacts_query = select(EvidenceArtifact).where(EvidenceArtifact.run_id == run_id)
    artifacts = (await session.execute(artifacts_query)).scalars().all()

    contexts_query = select(RunContext).where(RunContext.run_id == run_id)
    contexts = (await session.execute(contexts_query)).scalars().all()
    secret_keys = {c.key for c in contexts if c.is_secret}
    # Secret values are stored as [REDACTED] in DB; pass any found values to sanitization
    secret_values = {c.value for c in contexts if c.is_secret and c.value != "[REDACTED]"}

    # Set up destination paths:
    # 1. Per-run isolated directory: <output_dir>/runs/<run_id>/
    # 2. Base deliverable directory: <output_dir>/
    run_dir = base_out_dir / "runs" / run_id
    run_evidence_dir = run_dir / "evidence"
    root_evidence_dir = base_out_dir / "evidence"

    run_dir.mkdir(parents=True, exist_ok=True)
    run_evidence_dir.mkdir(parents=True, exist_ok=True)
    if export_root_deliverables:
        base_out_dir.mkdir(parents=True, exist_ok=True)
        root_evidence_dir.mkdir(parents=True, exist_ok=True)

    # 5. Process Evidence Artifacts & Extract Structured Step Telemetry
    # Map step_attempt_id -> artifacts
    step_artifacts_map: dict[str, list[EvidenceArtifact]] = {}
    copied_evidence_count = 0
    exported_evidence_refs: dict[str, list[TaskEvidenceRef]] = {}

    for art in artifacts:
        if art.step_attempt_id:
            step_artifacts_map.setdefault(art.step_attempt_id, []).append(art)

        # Copy artifact content to export directories
        ext = ".png" if art.type == "screenshot" else (".json" if "json" in (art.content_type or "") else ".bin")
        filename = f"{art.id}{ext}"
        rel_path = f"evidence/{filename}"

        try:
            content = active_storage.read(art.object_key, art.checksum)
            _atomic_write_file(run_evidence_dir / filename, content)
            if export_root_deliverables:
                _atomic_write_file(root_evidence_dir / filename, content)
            copied_evidence_count += 1
        except Exception as read_exc:
            logger.debug("Evidence artifact %s could not be read for export: %s", art.id, read_exc)

        ref = TaskEvidenceRef(
            artifact_id=art.id,
            type=art.type,
            relative_path=rel_path,
            checksum=art.checksum,
            size_bytes=art.size_bytes,
            content_type=art.content_type,
        )
        if art.step_attempt_id:
            exported_evidence_refs.setdefault(art.step_attempt_id, []).append(ref)

    # 6. Parse Step Details from Response Logs / Artifacts
    step_logs_by_step_id: dict[str, StepExecutionLog] = {}
    for sa in step_attempts:
        sa_arts = step_artifacts_map.get(sa.id, [])
        resp_art = next((a for a in sa_arts if a.type == "response_log"), None)
        inputs_summary: dict[str, Any] = {}
        outputs_summary: dict[str, Any] = {}
        http_status: int | None = None
        assertions_list: list[AssertionResultSummary] = []
        ui_outcome: dict[str, Any] | None = None
        action_name = "action"

        if resp_art:
            try:
                raw_bytes = active_storage.read(resp_art.object_key, resp_art.checksum)
                parsed = json.loads(raw_bytes.decode("utf-8"))
                action_name = parsed.get("action", action_name)
                req_data = parsed.get("request", {})
                resp_data = parsed.get("response", {})
                inputs_summary = _sanitize_data(req_data, secret_values)
                outputs_summary = _sanitize_data(resp_data, secret_values)
                http_status = resp_data.get("status_code")

                # Parse assertions
                raw_assertions = parsed.get("assertions")
                assertion_items = []
                if isinstance(raw_assertions, dict):
                    assertion_items = raw_assertions.get("results", [])
                elif isinstance(raw_assertions, list):
                    assertion_items = raw_assertions

                for a_res in assertion_items:
                    if isinstance(a_res, dict):
                        assertions_list.append(
                            AssertionResultSummary(
                                assertion_type=a_res.get("type", "status"),
                                status="passed" if a_res.get("passed") else "failed",
                                target=a_res.get("target"),
                                expected=a_res.get("expected"),
                                actual=a_res.get("actual"),
                                message=a_res.get("message"),
                            )
                        )
            except Exception as parse_exc:
                logger.debug("Failed parsing response_log for step attempt %s: %s", sa.id, parse_exc)

        # Check for UI action events in timeline
        for ev in events:
            if ev.step_id == sa.step_id and ev.event_metadata:
                if "locator" in ev.event_metadata or "action" in ev.event_metadata:
                    ui_outcome = {
                        "action": ev.event_metadata.get("action", action_name),
                        "locator": ev.event_metadata.get("locator"),
                        "outcome": sa.status,
                    }

        dur_ms = ((sa.finished_at - sa.started_at).total_seconds() * 1000.0) if sa.started_at and sa.finished_at else 0.0

        step_log = StepExecutionLog(
            step_id=sa.step_id,
            action=action_name,
            adapter_type="http_api" if http_status is not None else "web",
            status=sa.status,
            started_at=sa.started_at,
            finished_at=sa.finished_at,
            duration_ms=dur_ms,
            error_message=sa.error_message,
            inputs_summary=inputs_summary,
            outputs_summary=outputs_summary,
            http_status=http_status,
            ui_action_outcome=ui_outcome,
            assertions=assertions_list,
            evidence_references=exported_evidence_refs.get(sa.id, []),
        )
        step_logs_by_step_id[sa.step_id] = step_log

    # 7. Build Task Execution Logs (DAG Tasks or Single-Plan Task)
    task_logs: list[TaskExecutionLog] = []

    if plan_tasks:
        for pt in plan_tasks:
            attempt = next((a for a in task_attempts if a.task_id == pt.task_id), None)
            pt_step_ids = [s.get("step_id") for s in pt.steps if isinstance(s, dict)]
            matched_steps = [step_logs_by_step_id[sid] for sid in pt_step_ids if sid in step_logs_by_step_id]

            # Collect evidence for this task
            task_evidence: list[TaskEvidenceRef] = []
            for s in matched_steps:
                task_evidence.extend(s.evidence_references)

            t_status = attempt.status if attempt else "untested"
            t_started = attempt.started_at if attempt else None
            t_finished = attempt.finished_at if attempt else None
            t_err = attempt.error_message if attempt else None
            t_dur = ((t_finished - t_started).total_seconds() * 1000.0) if t_started and t_finished else 0.0

            err_category = _determine_error_category(t_status, t_err, matched_steps, events)
            diag = t_err if t_status in ("failed", "blocked") else None
            if t_status == "failed" and not diag:
                diag = "[INSUFFICIENT_EVIDENCE] Root cause cannot be conclusively verified from telemetry."

            task_logs.append(
                TaskExecutionLog(
                    task_id=pt.task_id,
                    name=pt.name,
                    role=pt.assigned_role,
                    adapter_type=pt.adapter_type,
                    status=t_status,
                    attempt_number=attempt.attempt_number if attempt else 1,
                    depends_on=task_deps_map.get(pt.task_id, []),
                    requirement_refs=pt.requirement_refs or [],
                    priority=getattr(pt, "priority", "P1") or "P1",
                    is_smoke=bool(getattr(pt, "is_smoke", False)),
                    is_regression=bool(getattr(pt, "is_regression", True)),
                    started_at=t_started,
                    finished_at=t_finished,
                    duration_ms=t_dur,
                    steps=matched_steps,
                    retries_count=max(0, (attempt.attempt_number - 1) if attempt else 0),
                    error_category=err_category,
                    diagnostic=diag,
                    evidence_references=task_evidence,
                )
            )
    else:
        # Single-plan run: represent execution as a single unified task
        all_steps = list(step_logs_by_step_id.values())
        task_evidence = [ref for s in all_steps for ref in s.evidence_references]
        err_cat = _determine_error_category(run.status, None, all_steps, events)
        diag = "[INSUFFICIENT_EVIDENCE] Run failed without specific task diagnostics." if run.status == "failed" else None

        task_logs.append(
            TaskExecutionLog(
                task_id="main_execution",
                name=plan_name,
                role="executor",
                adapter_type=plan.adapter_type if plan else "http_api",
                status=run.status,
                attempt_number=1,
                depends_on=[],
                requirement_refs=[],
                priority="P1",
                is_smoke=False,
                is_regression=True,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_ms=((run.finished_at - run.started_at).total_seconds() * 1000.0) if run.started_at and run.finished_at else 0.0,
                steps=all_steps,
                retries_count=0,
                error_category=err_cat,
                diagnostic=diag,
                evidence_references=task_evidence,
            )
        )

    # 8. Compute Requirement Traceability Matrix
    req_coverage_map: dict[str, list[str]] = {}
    for t in task_logs:
        for r_id in t.requirement_refs:
            req_coverage_map.setdefault(r_id, []).append(t.task_id)

    req_traceability: list[RequirementTraceabilityItem] = []
    for req_id, associated_tasks in req_coverage_map.items():
        matched_tasks = [t for t in task_logs if t.task_id in associated_tasks]
        if all(t.status == "passed" for t in matched_tasks):
            req_st = "PASSED"
            notes = "All verifying tasks completed successfully."
        elif any(t.status == "failed" for t in matched_tasks):
            failed_names = [t.task_id for t in matched_tasks if t.status == "failed"]
            req_st = "FAILED"
            notes = f"Failed in task(s): {', '.join(failed_names)}."
        elif any(t.status == "blocked" for t in matched_tasks):
            req_st = "BLOCKED"
            notes = "Verifying task(s) were blocked by prerequisite failures."
        elif all(t.status == "skipped" for t in matched_tasks):
            req_st = "UNTESTED"
            notes = f"Excluded from '{getattr(run, 'suite_type', 'all') or 'all'}' suite execution."
        else:
            req_st = "UNTESTED"
            notes = "Tasks did not complete execution."

        req_traceability.append(
            RequirementTraceabilityItem(
                requirement_id=req_id,
                status=req_st,
                tested_by_tasks=associated_tasks,
                notes=notes,
            )
        )

    # 9. Extract Retry History & Events
    retries: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []
    for ev in events:
        raw_events.append({
            "event_id": ev.id,
            "event_type": ev.event_type,
            "timestamp": ev.timestamp.isoformat(),
            "step_id": ev.step_id,
            "attempt_id": ev.attempt_id,
            "message": ev.message,
            "metadata": _sanitize_data(ev.event_metadata, secret_values),
        })
        if ev.event_type == "step_retry_scheduled" and ev.event_metadata:
            retries.append({
                "step_id": ev.step_id,
                "attempt_number": ev.event_metadata.get("attempt_number"),
                "next_attempt": ev.event_metadata.get("next_attempt"),
                "delay_seconds": ev.event_metadata.get("delay_seconds"),
                "message": ev.message,
            })

    # Summary Statistics
    total_tasks = len(task_logs)
    passed_tasks = sum(1 for t in task_logs if t.status == "passed")
    failed_tasks = sum(1 for t in task_logs if t.status == "failed")
    blocked_tasks = sum(1 for t in task_logs if t.status == "blocked")
    skipped_tasks = total_tasks - (passed_tasks + failed_tasks + blocked_tasks)

    duration_s = (
        (run.finished_at - run.started_at).total_seconds()
        if run.started_at and run.finished_at
        else None
    )

    # 10. Instantiate and Validate ExecutionLogSchema
    log_schema = ExecutionLogSchema(
        schema_version="1.0",
        run_id=run.id,
        mission_id=run.plan_id,
        plan_version=run.plan_version,
        environment_id=run.environment_id,
        project_id=run.project_id,
        suite_type=getattr(run, "suite_type", "all") or "all",
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=duration_s,
        summary={
            "total_tasks": total_tasks,
            "passed_tasks": passed_tasks,
            "failed_tasks": failed_tasks,
            "blocked_tasks": blocked_tasks,
            "skipped_tasks": skipped_tasks,
        },
        requirement_traceability=req_traceability,
        tasks=task_logs,
        retry_history=retries,
        timeline_events=raw_events,
        metadata={
            "exported_at": datetime.now(UTC).isoformat(),
            **(
                {"evaluation": next((ev.event_metadata for ev in events if ev.event_type == "evaluation_completed" and ev.event_metadata), None)}
                if any(ev.event_type == "evaluation_completed" for ev in events)
                else {}
            ),
        },
    )

    # 11. Generate Markdown Report
    report_md = _generate_markdown_report(log_schema, plan_name, getattr(run, "environment_id", None))

    # 12. Atomic Write of Deliverable Files
    log_json_content = json.dumps(log_schema.model_dump(mode="json"), indent=2)

    # Write to per-run isolated folder
    _atomic_write_file(run_dir / "execution_log.json", log_json_content)
    _atomic_write_file(run_dir / "report.md", report_md)

    # Write to base deliverable paths (/output/execution_log.json and /output/report.md)
    if export_root_deliverables:
        _atomic_write_file(base_out_dir / "execution_log.json", log_json_content)
        _atomic_write_file(base_out_dir / "report.md", report_md)

    logger.info(
        "Successfully exported execution log and report for run %s to %s",
        run.id,
        run_dir,
    )

    return ExportResult(
        run_id=run.id,
        output_dir=str(run_dir),
        execution_log_path=str(run_dir / "execution_log.json"),
        report_path=str(run_dir / "report.md"),
        evidence_files_count=copied_evidence_count,
        exported_at=datetime.now(UTC),
    )
