"""
uitp_orchestrator.dag_scheduler
===============================
Deterministic DAG scheduling, dynamic context variable resolution,
and failure cascade logic for multi-agent autonomous testing.

SECURITY INVARIANTS:
1. Dynamic context variables (${context.<key>}) are resolved in memory at runtime.
2. Raw runtime values are never passed back into LLM prompts.
3. Secret-flagged variables are marked for redaction in all persistent storage and logs.
4. Upstream task failure immediately cascades to BLOCKED for all downstream tasks.
"""
from __future__ import annotations

from uitp_contracts.enums import TaskPriority, TaskStatus, TestSuiteType
from uitp_contracts.plan import ContextExportDef, DAGTaskSchema
from uitp_orchestrator.context import (
    CONTEXT_VAR_REGEX,
    RuntimeContext,
    extract_context_exports,
    extract_task_exports,
    redact_sensitive_values,
    resolve_context_expressions,
)


def compute_suite_execution_subgraph(
    tasks: list[DAGTaskSchema],
    suite_type: TestSuiteType | str = TestSuiteType.ALL,
) -> tuple[set[str], set[str], set[str]]:
    """Compute task execution partition for the requested test suite.

    Returns:
      (selected_target_task_ids, prerequisite_ancestor_ids, skipped_task_ids)

    Rules:
      1. 'all': Every task is a selected target. Prerequisite and skipped sets are empty.
      2. 'smoke': Selects tasks where task.is_smoke is True or task.priority == TaskPriority.P0.
         Any required dependencies (direct or transitive ancestors) outside the smoke set are
         automatically included as prerequisite tasks to ensure dependency closure without silent failure.
      3. 'regression': Selects tasks where task.is_regression is True (or priority in P0, P1, P2).
         Any required dependencies are included as prerequisites.
      4. Tasks not in selected_targets or prerequisite_ancestors are skipped.
    """
    suite_str = str(suite_type).lower()
    valid_suites = {s.value for s in TestSuiteType}
    if suite_str not in valid_suites:
        raise ValueError(
            f"Unsupported suite filter '{suite_type}'. Supported suite types: {', '.join(sorted(valid_suites))}"
        )

    all_task_ids = {t.task_id for t in tasks}
    parent_map = {t.task_id: list(t.depends_on) for t in tasks}

    if suite_str == TestSuiteType.ALL.value:
        return set(all_task_ids), set(), set()

    # Identify initial direct targets for the suite
    targets: set[str] = set()
    for t in tasks:
        p_val = getattr(t, "priority", TaskPriority.P1)
        p_str = p_val.value if hasattr(p_val, "value") else str(p_val)
        is_smoke = getattr(t, "is_smoke", False) or p_str == "P0"
        is_regression = getattr(t, "is_regression", True) or p_str in ("P0", "P1", "P2")

        if suite_str == TestSuiteType.SMOKE.value and is_smoke:
            targets.add(t.task_id)
        elif suite_str == TestSuiteType.REGRESSION.value and is_regression:
            targets.add(t.task_id)

    # Compute transitive prerequisite closure of the selected targets
    prerequisites: set[str] = set()
    queue = list(targets)
    visited = set(targets)

    while queue:
        curr_id = queue.pop(0)
        for parent_id in parent_map.get(curr_id, []):
            if parent_id in all_task_ids and parent_id not in visited:
                visited.add(parent_id)
                prerequisites.add(parent_id)
                queue.append(parent_id)

    # Clean prerequisites set: do not overlap with direct targets
    prerequisites = prerequisites - targets
    active = targets | prerequisites
    skipped = all_task_ids - active

    return targets, prerequisites, skipped


class DAGTaskScheduler:
    """Manages DAG dependency state, ready task queues, and failure cascade transitions."""

    def __init__(
        self,
        tasks: list[DAGTaskSchema],
        active_task_ids: set[str] | None = None,
    ) -> None:
        self.tasks = {t.task_id: t for t in tasks}
        self.active_task_ids = set(active_task_ids) if active_task_ids is not None else {t.task_id for t in tasks}
        self.task_statuses: dict[str, TaskStatus] = {}
        self.in_degree: dict[str, int] = {}
        self.children_map: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for t in tasks:
            for p in t.depends_on:
                if p in self.children_map:
                    self.children_map[p].append(t.task_id)

        for t in tasks:
            tid = t.task_id
            if tid not in self.active_task_ids:
                self.task_statuses[tid] = TaskStatus.SKIPPED
                self.in_degree[tid] = 0
            else:
                self.task_statuses[tid] = TaskStatus.PENDING
                # In-degree counts parent dependencies that are active
                self.in_degree[tid] = sum(1 for p in t.depends_on if p in self.active_task_ids)

        # Active tasks with 0 dependencies start in READY state
        for tid in self.active_task_ids:
            if self.in_degree.get(tid, 0) == 0:
                self.task_statuses[tid] = TaskStatus.READY

    def get_ready_tasks(self) -> list[DAGTaskSchema]:
        """Return all tasks that currently have all dependencies met and are READY."""
        return [self.tasks[tid] for tid, status in self.task_statuses.items() if status == TaskStatus.READY]

    def mark_task_running(self, task_id: str) -> None:
        """Transition task from READY to RUNNING."""
        if self.task_statuses.get(task_id) == TaskStatus.READY:
            self.task_statuses[task_id] = TaskStatus.RUNNING

    def mark_task_passed(self, task_id: str) -> list[DAGTaskSchema]:
        """Transition task to PASSED, evaluate children, and return newly READY tasks."""
        self.task_statuses[task_id] = TaskStatus.PASSED
        newly_ready: list[DAGTaskSchema] = []

        for child_id in self.children_map.get(task_id, []):
            if self.task_statuses[child_id] == TaskStatus.PENDING:
                self.in_degree[child_id] -= 1
                if self.in_degree[child_id] == 0:
                    self.task_statuses[child_id] = TaskStatus.READY
                    newly_ready.append(self.tasks[child_id])

        return newly_ready

    def mark_task_failed(self, task_id: str) -> list[str]:
        """Transition task to FAILED and cascade BLOCKED status to all downstream tasks."""
        self.task_statuses[task_id] = TaskStatus.FAILED
        blocked_tasks: list[str] = []

        # Cascade BLOCKED to all downstream descendants (BFS)
        queue = list(self.children_map.get(task_id, []))
        while queue:
            child = queue.pop(0)
            if self.task_statuses[child] in (TaskStatus.PENDING, TaskStatus.READY):
                self.task_statuses[child] = TaskStatus.BLOCKED
                blocked_tasks.append(child)
                queue.extend(self.children_map.get(child, []))

        return blocked_tasks

    def mark_task_cancelled(self, task_id: str) -> None:
        """Mark task as CANCELLED."""
        self.task_statuses[task_id] = TaskStatus.CANCELLED

    def cancel_all_pending(self) -> None:
        """Cancel all pending or ready tasks."""
        for tid, status in self.task_statuses.items():
            if status in (TaskStatus.PENDING, TaskStatus.READY):
                self.task_statuses[tid] = TaskStatus.CANCELLED

    def is_finished(self) -> bool:
        """Return True when all tasks are in a terminal state."""
        terminal_statuses = {
            TaskStatus.PASSED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
        }
        return all(s in terminal_statuses for s in self.task_statuses.values())
