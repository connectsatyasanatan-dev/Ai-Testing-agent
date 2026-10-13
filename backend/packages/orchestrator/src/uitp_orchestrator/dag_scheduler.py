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

import re
from typing import Any

from uitp_contracts.enums import TaskStatus
from uitp_contracts.plan import ContextExportDef, DAGTaskSchema

CONTEXT_VAR_REGEX = re.compile(r"\$\{context\.([a-zA-Z0-9_]+)\}")


def resolve_context_expressions(obj: Any, context_values: dict[str, Any]) -> Any:
    """Recursively resolve '${context.<key>}' expressions against context_values.

    Leaves unresolvable expressions unchanged.
    """
    if isinstance(obj, str):
        def _replace_match(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name in context_values:
                val = context_values[var_name]
                return str(val)
            return match.group(0)

        return CONTEXT_VAR_REGEX.sub(_replace_match, obj)

    elif isinstance(obj, dict):
        return {k: resolve_context_expressions(v, context_values) for k, v in obj.items()}

    elif isinstance(obj, list):
        return [resolve_context_expressions(item, context_values) for item in obj]

    return obj


def extract_context_exports(
    step_output: dict[str, Any],
    exports_def: dict[str, ContextExportDef],
) -> dict[str, tuple[str, bool]]:
    """Extract exported context variables from step outputs.

    Returns:
        Mapping of export_key -> (string_value, is_secret).
    """
    extracted: dict[str, tuple[str, bool]] = {}

    for export_key, export_def in exports_def.items():
        json_path = export_def.json_path.strip()
        # Simple JSON path resolution for standard expressions (e.g. $.id, $.token, $.data.id)
        path_parts = json_path.lstrip("$.").split(".")
        current: Any = step_output
        found = True

        for part in path_parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break

        if found and current is not None:
            extracted[export_key] = (str(current), export_def.is_secret)

    return extracted


class DAGTaskScheduler:
    """Manages DAG dependency state, ready task queues, and failure cascade transitions."""

    def __init__(self, tasks: list[DAGTaskSchema]) -> None:
        self.tasks = {t.task_id: t for t in tasks}
        self.task_statuses: dict[str, TaskStatus] = {t.task_id: TaskStatus.PENDING for t in tasks}
        self.in_degree: dict[str, int] = {t.task_id: len(t.depends_on) for t in tasks}
        self.children_map: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for t in tasks:
            for p in t.depends_on:
                if p in self.children_map:
                    self.children_map[p].append(t.task_id)

        # Tasks with 0 dependencies start in READY state
        for tid, deg in self.in_degree.items():
            if deg == 0:
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
