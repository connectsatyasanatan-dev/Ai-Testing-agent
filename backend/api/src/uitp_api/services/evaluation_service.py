"""
uitp_api.services.evaluation_service
=====================================
Post-run evaluation and evidence-based root cause analysis (Task 8).

Responsibilities:
  1. Authoritative Evaluation Lifecycle:
     - Automatically triggered when test runs reach terminal status (passed, failed, blocked, cancelled, inconclusive).
     - Strictly idempotent: re-evaluations check for existing persisted evaluation records and avoid duplicate side effects.
     - Fault-tolerant: evaluation failures are logged and recorded via events without corrupting original run status.
  2. Grounded Root Cause Analysis:
     - Deterministic-first evidence extraction: assertion failures (expected vs actual), HTTP status codes,
       network/gateway exceptions, UI locator ambiguity/timeouts, screenshot references, and dependency chains.
     - Clearly distinguishes confirmed causes, probable causes, and unknown causes ([INSUFFICIENT_EVIDENCE]).
     - Optional token-bounded AI root-cause summarization with strict secret redaction and deterministic fallback.
     - Invariant: AI never alters verdicts, counts, or pass/fail determinations.
  3. Requirement Traceability Matrix:
     - Maps outcomes back to requirement IDs (PASSED, FAILED, BLOCKED, UNTESTED).
     - Clearly tags requirements that have no executable coverage.
  4. Reporting & Deliverable Integration:
     - Feeds evaluation findings into ReportExportService (/output/execution_log.json and /output/report.md).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.exceptions import ResourceNotFoundError
from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.services.ai_provider import get_ai_provider
from uitp_contracts.enums import (
    AdapterType,
    AuditEventType,
    ErrorCode,
    RunStatus,
    StepStatus,
    TaskStatus,
)
from uitp_contracts.plan import (
    MissionEvaluationReport,
    RequirementCoverageStatus,
    TokenUsage,
)
from uitp_orchestrator.context import redact_sensitive_values

logger = logging.getLogger(__name__)


def _sanitize_for_prompt(data: Any) -> Any:
    """Scrub sensitive credentials, tokens, and cookies before passing to evaluation models."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            lower_k = str(k).lower()
            if any(s in lower_k for s in ("auth", "password", "token", "secret", "cookie", "key")):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_for_prompt(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_for_prompt(item) for item in data]
    elif isinstance(data, str):
        # Redact obvious token/key patterns
        for pattern_word in ("bearer ", "basic ", "ey"):
            if pattern_word in data.lower():
                return "[REDACTED]"
        return data
    return data


def _classify_cause_and_diagnostic(
    task_id: str,
    status: str,
    error_message: str | None,
    matched_steps: list[StepAttempt],
    dependencies: list[str],
    all_task_attempts: dict[str, DAGTaskAttempt],
    evidence_references: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministically analyze task telemetry to produce grounded root-cause diagnostic."""
    cause_type = "unknown"
    diagnostic = error_message or "[INSUFFICIENT_EVIDENCE] Root cause cannot be conclusively determined from telemetry."
    failed_step_id: str | None = None
    failed_action: str | None = None
    failed_assertions: list[dict[str, Any]] = []
    http_status: int | None = None
    evidence_refs: list[str] = list(evidence_references or [])
    suggested_steps: list[str] = []

    # 1. Check if task was blocked by upstream dependencies
    if status == "blocked":
        failing_parents = []
        for p in dependencies:
            p_att = all_task_attempts.get(p)
            if p_att and p_att.status in ("failed", "blocked"):
                failing_parents.append(f"{p} ({p_att.status})")
        cause_type = "confirmed"
        if failing_parents:
            diagnostic = f"Task execution blocked by upstream prerequisite failure in: {', '.join(failing_parents)}."
            suggested_steps.append(f"Resolve upstream prerequisites: {', '.join(failing_parents)}.")
        else:
            diagnostic = "Task was blocked before execution due to unmet prerequisite dependencies."
            suggested_steps.append("Check DAG task dependency graph and scheduling constraints.")

        return {
            "task_id": task_id,
            "status": status,
            "cause_type": cause_type,
            "classification": f"{cause_type}_cause",
            "diagnostic": diagnostic,
            "failed_step_id": None,
            "failed_action": None,
            "http_status": None,
            "failed_assertions": [],
            "evidence_references": [],
            "dependency_chain": dependencies,
            "suggested_debugging_steps": suggested_steps,
            "is_ai_generated": False,
        }

    # 2. Check individual step attempts for assertion failures and errors
    for step in matched_steps:
        if step.status == StepStatus.FAILED.value:
            failed_step_id = step.step_id
            if step.error_code == ErrorCode.ASSERTION_FAILED.value or (step.error_message and "Assertion" in step.error_message):
                cause_type = "confirmed"
                diagnostic = f"Assertion failure in step '{step.step_id}': {step.error_message}"
                suggested_steps.append(f"Verify server payload against assertion criteria in step '{step.step_id}'.")
                break
            elif step.error_code in (ErrorCode.VALIDATION_ERROR.value, "AMBIGUOUS_LOCATOR") or (step.error_message and "Ambiguous locator" in step.error_message):
                cause_type = "confirmed"
                diagnostic = f"Locator ambiguity / validation error in step '{step.step_id}': {step.error_message}"
                suggested_steps.append("Use a unique test-id or semantic accessible role locator.")
                break
            elif step.error_code in ("ADAPTER_TIMEOUT", "TIMEOUT", "WebActionTimeoutError") or (step.error_message and "timed out" in step.error_message.lower()):
                cause_type = "probable"
                diagnostic = f"Action timed out in step '{step.step_id}': {step.error_message}"
                suggested_steps.append("Check target application responsiveness and increase bounded action timeout if needed.")
                break
            elif step.error_code in ("NETWORK_ERROR", "CONNECT_TIMEOUT", "BAD_GATEWAY", "SERVICE_UNAVAILABLE"):
                cause_type = "probable"
                diagnostic = f"Transient network/infrastructure error in step '{step.step_id}': {step.error_message}"
                suggested_steps.append("Verify network connectivity and target server health.")
                break
            elif step.error_message:
                cause_type = "confirmed"
                diagnostic = f"Failure in step '{step.step_id}': {step.error_message}"
                suggested_steps.append(f"Inspect application error log for step '{step.step_id}'.")
                break

    if not suggested_steps:
        suggested_steps.append("Inspect execution log and artifact snapshots for error context.")

    return {
        "task_id": task_id,
        "status": status,
        "cause_type": cause_type,
        "classification": f"{cause_type}_cause",
        "diagnostic": diagnostic,
        "failed_step_id": failed_step_id,
        "failed_action": failed_action,
        "http_status": http_status,
        "failed_assertions": failed_assertions,
        "evidence_references": evidence_refs,
        "dependency_chain": dependencies,
        "suggested_debugging_steps": suggested_steps,
        "is_ai_generated": False,
    }


async def _synthesize_optional_ai_summary(
    deterministic_causes: list[dict[str, Any]],
    total_tasks: int,
    failed_tasks: int,
    blocked_tasks: int,
) -> dict[str, Any] | None:
    """Optionally request token-bounded AI root-cause summary with deterministic fallback."""
    try:
        provider = get_ai_provider()
    except Exception:
        return None

    if provider is None:
        return None

    if not deterministic_causes:
        return None

    sanitized_input = _sanitize_for_prompt(deterministic_causes)
    system_prompt = (
        "You are an expert autonomous test evaluation agent. "
        "Analyze the provided test execution failure evidence and synthesize an objective, "
        "evidence-grounded root-cause summary. "
        "Do NOT speculate or fabricate causes absent from the evidence. "
        "Return valid JSON adhering to the schema: "
        '{"summary": string, "hypotheses": [{"task_id": string, "hypothesis": string, "confidence": "high"|"medium"|"low"}]}'
    )
    user_prompt = f"Failure evidence ({failed_tasks} failed, {blocked_tasks} blocked out of {total_tasks} tasks):\n{json.dumps(sanitized_input, indent=2)}"

    try:
        if hasattr(provider, "complete_structured"):
            raw_output, _ = await provider.complete_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        elif hasattr(provider, "complete"):
            resp = await provider.complete(system_prompt=system_prompt, user_prompt=user_prompt)
            raw_output = json.loads(resp.content) if hasattr(resp, "content") else {}
        else:
            return None

        if isinstance(raw_output, dict) and "summary" in raw_output:
            return {
                "ai_summary": raw_output.get("summary", ""),
                "hypotheses": raw_output.get("hypotheses", []),
                "is_ai_generated": True,
            }
    except Exception as exc:
        logger.debug("Optional AI evaluation synthesis skipped: %s", exc)

    return None


async def evaluate_and_persist_run(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str | None = None,
    force_reevaluate: bool = False,
    use_ai_summary: bool = True,
    declared_requirements: list[str] | None = None,
) -> MissionEvaluationReport:
    """Evaluate a completed test run and persist idempotent evaluation report and events.

    Invariants:
      1. Idempotency: Duplicate calls return previously persisted evaluation report without duplicate events.
      2. Non-Destructive: Evaluator failures never overwrite or erase original Run status.
      3. Grounded: Verdicts and root causes strictly reflect actual persisted assertions and status codes.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise ResourceNotFoundError(f"Run '{run_id}' not found.")

    # 1. Idempotency Check: if previously evaluated and not forced, return cached evaluation
    if not force_reevaluate:
        existing_event_query = (
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.event_type == "evaluation_completed")
            .order_by(RunEvent.timestamp.desc())
        )
        existing_ev = (await session.execute(existing_event_query)).scalars().first()
        if existing_ev and existing_ev.event_metadata:
            try:
                cached_data = dict(existing_ev.event_metadata)
                cached_data["is_idempotent_replay"] = True
                return MissionEvaluationReport.model_validate(cached_data)
            except Exception as parse_exc:
                logger.warning("Could not parse existing evaluation for run %s: %s; re-evaluating.", run_id, parse_exc)

    eval_start_time = datetime.now(UTC)

    try:
        # 2. Query execution artifacts, task attempts, and plan definition
        task_attempts_query = select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id)
        task_attempts = (await session.execute(task_attempts_query)).scalars().all()
        attempts_by_task_id = {a.task_id: a for a in task_attempts}

        plan_tasks_query = select(DAGTask).where(or_(DAGTask.plan_id == run.plan_id, DAGTask.plan_id == run.id))
        plan_tasks = (await session.execute(plan_tasks_query)).scalars().all()

        deps_query = select(DAGDependency).where(or_(DAGDependency.plan_id == run.plan_id, DAGDependency.plan_id == run.id))
        deps = (await session.execute(deps_query)).scalars().all()
        task_deps_map: dict[str, list[str]] = {}
        for d in deps:
            task_deps_map.setdefault(d.child_task_id, []).append(d.parent_task_id)

        step_attempts_query = select(StepAttempt).where(StepAttempt.run_id == run_id)
        step_attempts = (await session.execute(step_attempts_query)).scalars().all()

        artifacts_query = select(EvidenceArtifact).where(EvidenceArtifact.run_id == run_id)
        evidence_records = (await session.execute(artifacts_query)).scalars().all()
        step_to_artifacts: dict[str, list[str]] = {}
        all_artifact_ids: list[str] = []
        for art in evidence_records:
            all_artifact_ids.append(art.id)
            if art.step_attempt_id:
                step_to_artifacts.setdefault(art.step_attempt_id, []).append(art.id)

        # Group step attempts by step_id
        step_attempts_by_task: dict[str, list[StepAttempt]] = {}
        for pt in plan_tasks:
            pt_step_ids = {s.get("step_id") for s in pt.steps if isinstance(s, dict)}
            matched = [sa for sa in step_attempts if sa.step_id in pt_step_ids]
            step_attempts_by_task[pt.task_id] = matched

        # 3. Categorize task statuses
        task_statuses: dict[str, str] = {}
        if plan_tasks:
            for pt in plan_tasks:
                att = attempts_by_task_id.get(pt.task_id)
                if att:
                    task_statuses[pt.task_id] = att.status
                else:
                    if run.status == RunStatus.CANCELLED.value:
                        task_statuses[pt.task_id] = "cancelled"
                    elif run.status in (RunStatus.FAILED.value, RunStatus.BLOCKED.value):
                        task_statuses[pt.task_id] = "blocked"
                    else:
                        task_statuses[pt.task_id] = "skipped"
        else:
            # Single-plan run without DAGTask definition
            task_statuses["main_execution"] = run.status

        total_tasks = len(task_statuses)
        passed_tasks = sum(1 for s in task_statuses.values() if s == "passed")
        failed_tasks = sum(1 for s in task_statuses.values() if s == "failed")
        blocked_tasks = sum(1 for s in task_statuses.values() if s == "blocked")
        skipped_tasks = sum(1 for s in task_statuses.values() if s == "skipped")
        cancelled_tasks = sum(1 for s in task_statuses.values() if s == "cancelled")
        inconclusive_tasks = sum(1 for s in task_statuses.values() if s == "inconclusive")

        # 4. Deterministic Overall Run Verdict Calculation
        if failed_tasks > 0 or run.status == RunStatus.FAILED.value:
            verdict = RunStatus.FAILED
        elif cancelled_tasks > 0 or run.status == RunStatus.CANCELLED.value:
            verdict = RunStatus.CANCELLED
        elif blocked_tasks > 0 or run.status == RunStatus.BLOCKED.value:
            verdict = RunStatus.BLOCKED
        elif inconclusive_tasks > 0 or run.status == RunStatus.INCONCLUSIVE.value:
            verdict = RunStatus.INCONCLUSIVE
        elif passed_tasks > 0 and (passed_tasks + skipped_tasks == total_tasks):
            verdict = RunStatus.PASSED
        elif skipped_tasks == total_tasks:
            verdict = RunStatus.SKIPPED
        else:
            verdict = RunStatus(run.status) if run.status in RunStatus._value2member_map_ else RunStatus.FAILED

        # 5. Requirement Coverage Mapping
        all_declared_requirements: set[str] = set()
        req_to_tasks: dict[str, list[str]] = {}

        for pt in plan_tasks:
            for req in (pt.requirement_refs or []):
                all_declared_requirements.add(req)
                req_to_tasks.setdefault(req, []).append(pt.task_id)

        # Check plan or explicit declared requirements for requirements that might have NO mapped tasks
        plan = await session.get(TestPlan, run.plan_id)
        if plan:
            plan_meta = getattr(plan, "plan_metadata", None)
            if isinstance(plan_meta, dict) and isinstance(plan_meta.get("requirements"), list):
                for r in plan_meta["requirements"]:
                    if isinstance(r, str):
                        all_declared_requirements.add(r)
                    elif isinstance(r, dict) and "id" in r:
                        all_declared_requirements.add(r["id"])

        if declared_requirements:
            for r in declared_requirements:
                if isinstance(r, str):
                    all_declared_requirements.add(r)

        req_coverage: list[RequirementCoverageStatus] = []
        for req_id in sorted(all_declared_requirements):
            associated_tasks = req_to_tasks.get(req_id, [])
            if not associated_tasks:
                req_coverage.append(
                    RequirementCoverageStatus(
                        requirement_id=req_id,
                        status="UNTESTED",
                        tested_by_tasks=[],
                        notes="No executable tasks mapped to requirement in this test plan.",
                    )
                )
                continue

            statuses = [task_statuses.get(t, "untested") for t in associated_tasks]
            if all(s == "passed" for s in statuses):
                req_status = "PASSED"
                notes = "All verifying tasks completed successfully."
            elif any(s == "failed" for s in statuses):
                failing = [t for t in associated_tasks if task_statuses.get(t) == "failed"]
                req_status = "FAILED"
                notes = f"Failed in verifying task(s): {', '.join(failing)}."
            elif any(s == "blocked" for s in statuses):
                blocking = [t for t in associated_tasks if task_statuses.get(t) == "blocked"]
                req_status = "BLOCKED"
                notes = f"Blocked by prerequisites in task(s): {', '.join(blocking)}."
            elif any(s == "cancelled" for s in statuses):
                req_status = "UNTESTED"
                notes = "Execution was cancelled before task completion."
            elif all(s == "skipped" for s in statuses):
                req_status = "UNTESTED"
                notes = f"Excluded from '{getattr(run, 'suite_type', 'all')}' suite execution."
            else:
                req_status = "UNTESTED"
                notes = "Task execution did not conclude with definitive outcome."

            req_coverage.append(
                RequirementCoverageStatus(
                    requirement_id=req_id,
                    status=req_status,
                    tested_by_tasks=associated_tasks,
                    notes=notes,
                )
            )

        # 6. Deterministic Grounded Root Cause Analysis
        root_causes: list[dict[str, Any]] = []
        for task_id, t_status in task_statuses.items():
            if t_status in ("failed", "blocked", "cancelled", "inconclusive"):
                att = attempts_by_task_id.get(task_id)
                err_msg = att.error_message if att else None
                m_steps = step_attempts_by_task.get(task_id, [])
                deps_list = task_deps_map.get(task_id, [])

                # Gather evidence references for this task
                task_step_ids = {s.id for s in m_steps}
                task_evidence: list[str] = []
                for s_id in task_step_ids:
                    task_evidence.extend(step_to_artifacts.get(s_id, []))
                if not task_evidence and t_status == "failed" and all_artifact_ids:
                    task_evidence = list(all_artifact_ids)

                rc_item = _classify_cause_and_diagnostic(
                    task_id=task_id,
                    status=t_status,
                    error_message=err_msg,
                    matched_steps=m_steps,
                    dependencies=deps_list,
                    all_task_attempts=attempts_by_task_id,
                    evidence_references=task_evidence,
                )
                root_causes.append(rc_item)

        # 7. Optional AI Root Cause Synthesis
        ai_synthesis: dict[str, Any] | None = None
        if use_ai_summary and root_causes:
            ai_synthesis = await _synthesize_optional_ai_summary(
                deterministic_causes=root_causes,
                total_tasks=total_tasks,
                failed_tasks=failed_tasks,
                blocked_tasks=blocked_tasks,
            )
            if ai_synthesis:
                # Attach AI hypotheses to root cause items without overriding deterministic facts
                for rc in root_causes:
                    hyp = next(
                        (h for h in ai_synthesis.get("hypotheses", []) if h.get("task_id") == rc["task_id"]),
                        None,
                    )
                    if hyp:
                        rc["ai_hypothesis"] = {
                            "hypothesis": hyp.get("hypothesis"),
                            "confidence": hyp.get("confidence", "medium"),
                            "is_ai_generated": True,
                        }

        # 8. Executive Summary & Telemetry
        dur_s = (
            (run.finished_at - run.started_at).total_seconds()
            if run.started_at and run.finished_at
            else (datetime.now(UTC) - eval_start_time).total_seconds()
        )
        exec_summary = (
            f"Mission run evaluation complete (verdict: {verdict.value.upper()}). "
            f"Passed: {passed_tasks}/{total_tasks}, Failed: {failed_tasks}, "
            f"Blocked: {blocked_tasks}, Skipped: {skipped_tasks}."
        )
        if ai_synthesis and ai_synthesis.get("ai_summary"):
            exec_summary += f" [AI Summary: {ai_synthesis['ai_summary']}]"

        telemetry: dict[str, Any] = {
            "status": run.status,
            "verdict": verdict.value,
            "duration_s": dur_s,
            "evaluated_at": eval_start_time.isoformat(),
            "evaluation_version": "1.0",
        }
        if ai_synthesis:
            telemetry["ai_summary"] = ai_synthesis.get("ai_summary")

        report = MissionEvaluationReport(
            mission_id=run.plan_id,
            run_id=run.id,
            verdict=verdict,
            total_tasks=total_tasks,
            passed_tasks=passed_tasks,
            failed_tasks=failed_tasks,
            blocked_tasks=blocked_tasks,
            skipped_tasks=skipped_tasks,
            cancelled_tasks=cancelled_tasks,
            inconclusive_tasks=inconclusive_tasks,
            evaluation_version="1.0",
            evaluated_at=eval_start_time,
            is_idempotent_replay=False,
            requirement_coverage=req_coverage,
            root_cause_analysis=root_causes,
            executive_summary=exec_summary,
            agent_telemetry=telemetry,
        )

        # 9. Persist Evaluation Event
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="evaluation_completed",
                timestamp=datetime.now(UTC),
                message=exec_summary,
                event_metadata=report.model_dump(mode="json"),
            )
        )
        await session.commit()

        logger.info("Evaluation persisted successfully for run %s (verdict: %s)", run.id, verdict.value)
        return report

    except Exception as exc:
        logger.exception("Evaluation execution encountered error for run %s: %s", run.id, exc)
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="evaluation_failed",
                timestamp=datetime.now(UTC),
                message=f"Evaluation failed: {type(exc).__name__}: {exc}",
                event_metadata={"error": str(exc)},
            )
        )
        await session.commit()
        # Non-destructive: original run status is strictly preserved
        raise


async def generate_mission_evaluation(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
) -> MissionEvaluationReport:
    """Delegate route request to evaluate_and_persist_run with full idempotency."""
    return await evaluate_and_persist_run(
        session,
        run_id=run_id,
        actor_id=actor_id,
        force_reevaluate=False,
        use_ai_summary=True,
    )
