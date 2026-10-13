"""
tests/unit/test_dag_scheduler.py
================================
Unit tests for DAG dependency scheduling, dynamic context variable resolution,
and cascading failure propagation.
"""
from __future__ import annotations

import pytest

from uitp_contracts.enums import AdapterType, AgentRole, TaskStatus
from uitp_contracts.plan import ContextExportDef, DAGTaskSchema
from uitp_orchestrator.dag_scheduler import (
    DAGTaskScheduler,
    extract_context_exports,
    resolve_context_expressions,
)


def _make_task(task_id: str, depends_on: list[str] | None = None) -> DAGTaskSchema:
    return DAGTaskSchema(
        task_id=task_id,
        name=f"Task {task_id}",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        depends_on=depends_on or [],
    )


def test_resolve_context_expressions() -> None:
    context = {
        "user_id": "usr_9988",
        "auth_token": "secret_tok_123",
    }

    raw_inputs = {
        "url": "https://api.test/users/${context.user_id}/profile",
        "headers": {
            "Authorization": "Bearer ${context.auth_token}",
            "X-Static": "static_val",
        },
        "query": ["${context.user_id}", "fixed"],
    }

    resolved = resolve_context_expressions(raw_inputs, context)

    assert resolved["url"] == "https://api.test/users/usr_9988/profile"
    assert resolved["headers"]["Authorization"] == "Bearer secret_tok_123"
    assert resolved["headers"]["X-Static"] == "static_val"
    assert resolved["query"] == ["usr_9988", "fixed"]


def test_extract_context_exports() -> None:
    step_output = {
        "order": {
            "id": "ord_5544",
            "status": "created",
        },
        "token": "tok_xyz",
    }

    exports_def = {
        "order_id": ContextExportDef(json_path="$.order.id", is_secret=False),
        "auth_token": ContextExportDef(json_path="$.token", is_secret=True),
        "missing_var": ContextExportDef(json_path="$.nonexistent.field", is_secret=False),
    }

    extracted = extract_context_exports(step_output, exports_def)

    assert extracted["order_id"] == ("ord_5544", False)
    assert extracted["auth_token"] == ("tok_xyz", True)
    assert "missing_var" not in extracted


def test_dag_scheduler_independent_tasks_ready_immediately() -> None:
    t1 = _make_task("t1")
    t2 = _make_task("t2")
    t3 = _make_task("t3", depends_on=["t1", "t2"])

    scheduler = DAGTaskScheduler([t1, t2, t3])

    ready = scheduler.get_ready_tasks()
    ready_ids = {t.task_id for t in ready}
    assert ready_ids == {"t1", "t2"}
    assert scheduler.task_statuses["t3"] == TaskStatus.PENDING

    # Mark t1 running then passed
    scheduler.mark_task_running("t1")
    assert scheduler.task_statuses["t1"] == TaskStatus.RUNNING

    newly_ready = scheduler.mark_task_passed("t1")
    # t3 is still waiting on t2
    assert newly_ready == []
    assert scheduler.task_statuses["t3"] == TaskStatus.PENDING

    # Mark t2 passed -> t3 should become ready!
    scheduler.mark_task_running("t2")
    newly_ready_2 = scheduler.mark_task_passed("t2")
    assert len(newly_ready_2) == 1
    assert newly_ready_2[0].task_id == "t3"
    assert scheduler.task_statuses["t3"] == TaskStatus.READY


def test_dag_scheduler_failure_cascades_to_blocked() -> None:
    # A -> B -> C
    ta = _make_task("ta")
    tb = _make_task("tb", depends_on=["ta"])
    tc = _make_task("tc", depends_on=["tb"])

    scheduler = DAGTaskScheduler([ta, tb, tc])

    scheduler.mark_task_running("ta")
    blocked = scheduler.mark_task_failed("ta")

    assert "tb" in blocked
    assert "tc" in blocked
    assert scheduler.task_statuses["ta"] == TaskStatus.FAILED
    assert scheduler.task_statuses["tb"] == TaskStatus.BLOCKED
    assert scheduler.task_statuses["tc"] == TaskStatus.BLOCKED
    assert scheduler.is_finished() is True
