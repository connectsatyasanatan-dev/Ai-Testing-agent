"""
uitp_orchestrator.context
=========================
Run-scoped runtime context, dynamic context variable extraction,
dependency-scoped expression resolution, and sensitive data protection.

SECURITY INVARIANTS:
1. Dynamic context variables (${context.<key>}) are resolved in memory at runtime.
2. Raw runtime values are never passed back into LLM prompts.
3. Secret-flagged variables are marked for redaction in all persistent storage and logs.
4. Tasks can only read context exported by tasks they explicitly depend on (direct or transitive ancestors).
5. Unrelated tenants, runs, or concurrent missions cannot share runtime context.
"""
from __future__ import annotations

import asyncio
import copy
import re
from typing import Any, Mapping

from uitp_contracts.plan import ContextExportDef, DAGTaskSchema
from uitp_orchestrator.exceptions import (
    ContextTypeMismatchError,
    DisallowedContextReferenceError,
    DuplicateContextExportError,
    InvalidContextExpressionError,
    MissingContextExportError,
    MissingContextVariableError,
)

CONTEXT_VAR_REGEX = re.compile(r"\$\{context\.([a-zA-Z0-9_]+)\}")
TEMPLATE_CANDIDATE_REGEX = re.compile(r"\$\{([^}]+)\}")


def _resolve_json_path(data: Any, path: str) -> Any:
    """Safely traverse nested dictionaries and lists using dot/bracket syntax."""
    clean_path = path.strip().lstrip("$").lstrip(".")
    if not clean_path:
        return data

    tokens = re.findall(r"[^.\[\]]+", clean_path)
    current = data

    for token in tokens:
        if isinstance(current, dict):
            if token in current:
                current = current[token]
            else:
                return None
        elif isinstance(current, (list, tuple)):
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            else:
                return None
        else:
            return None

    return current


def extract_context_exports(
    step_output: dict[str, Any],
    exports_def: dict[str, ContextExportDef],
) -> dict[str, tuple[str, bool]]:
    """Extract exported context variables from step outputs.

    Inspects both the direct output dictionary and an inner 'body' dictionary
    (such as returned by HTTP API adapter responses).

    Returns:
        Mapping of export_key -> (string_value, is_secret).
    """
    extracted: dict[str, tuple[str, bool]] = {}

    for export_key, export_def in exports_def.items():
        val = _resolve_json_path(step_output, export_def.json_path)
        if val is None and isinstance(step_output.get("body"), dict):
            val = _resolve_json_path(step_output["body"], export_def.json_path)

        if val is not None:
            extracted[export_key] = (str(val), export_def.is_secret)

    return extracted


def extract_task_exports(
    task_schema: DAGTaskSchema,
    step_outputs: list[dict[str, Any]],
) -> dict[str, tuple[str, bool]]:
    """Extract and validate all exports declared by a task from its step outputs.

    Searches step outputs from most recent to oldest.

    Raises:
        MissingContextExportError: If any declared export cannot be resolved, is missing, or is null.
    """
    extracted: dict[str, tuple[str, bool]] = {}

    for export_key, export_def in task_schema.exports.items():
        found = False
        val_resolved = None

        for output in reversed(step_outputs):
            single = extract_context_exports(output, {export_key: export_def})
            if export_key in single:
                v, is_sec = single[export_key]
                if v is not None and v.strip() != "" and v != "None":
                    val_resolved = v
                    extracted[export_key] = (v, is_sec)
                    found = True
                    break

        if not found or val_resolved is None:
            raise MissingContextExportError(
                f"Task '{task_schema.task_id}' declared export '{export_key}' with JSONPath "
                f"'{export_def.json_path}', but the value was missing or null in step outputs."
            )

    return extracted


def resolve_context_expressions(obj: Any, context_values: dict[str, Any]) -> Any:
    """Recursively resolve '${context.<key>}' expressions against context_values.

    If an entire string is exactly '${context.<key>}', returns the context value
    preserving its native type (int, float, bool, str).
    Leaves unresolvable expressions unchanged.
    """
    if isinstance(obj, str):
        exact_match = re.fullmatch(r"\$\{context\.([a-zA-Z0-9_]+)\}", obj)
        if exact_match:
            var_name = exact_match.group(1)
            if var_name in context_values:
                return context_values[var_name]
            return obj

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


def redact_sensitive_values(data: Any, secrets: set[str]) -> Any:
    """Recursively redact known secret literal strings across payloads and objects."""
    if not secrets:
        return data

    if isinstance(data, str):
        res = data
        for s in secrets:
            if s and len(s) > 0 and s in res:
                res = res.replace(s, "[REDACTED]")
        return res

    elif isinstance(data, Mapping):
        return {k: redact_sensitive_values(v, secrets) for k, v in data.items()}

    elif isinstance(data, (list, tuple)):
        return [redact_sensitive_values(item, secrets) for item in data]

    return data


class RuntimeContext:
    """Run-scoped runtime context managing variable exports, dependency scoping,
    and concurrency-safe expression validation."""

    def __init__(
        self,
        run_id: str,
        tasks: list[DAGTaskSchema],
        project_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.tasks = {t.task_id: t for t in tasks}
        self._lock = asyncio.Lock()
        self._values: dict[str, Any] = {}
        self._is_secret: dict[str, bool] = {}
        self._producers: dict[str, str] = {}
        self._ancestors: dict[str, set[str]] = self._compute_ancestors(tasks)
        self._declared_exports: dict[str, str] = {}

        # Validate unique export key declarations across the mission DAG
        for task in tasks:
            for exp_key in task.exports.keys():
                if exp_key in self._declared_exports and self._declared_exports[exp_key] != task.task_id:
                    raise DuplicateContextExportError(
                        f"Duplicate declared export key '{exp_key}' across tasks: "
                        f"declared in '{self._declared_exports[exp_key]}' and '{task.task_id}'."
                    )
                self._declared_exports[exp_key] = task.task_id

    @staticmethod
    def _compute_ancestors(tasks: list[DAGTaskSchema]) -> dict[str, set[str]]:
        adj: dict[str, list[str]] = {t.task_id: list(t.depends_on) for t in tasks}
        ancestors: dict[str, set[str]] = {t.task_id: set() for t in tasks}
        for t in tasks:
            visited = set()
            stack = list(t.depends_on)
            while stack:
                parent = stack.pop()
                if parent not in visited:
                    visited.add(parent)
                    ancestors[t.task_id].add(parent)
                    stack.extend(adj.get(parent, []))
        return ancestors

    def get_ancestors(self, task_id: str) -> set[str]:
        return set(self._ancestors.get(task_id, set()))

    def get_producer(self, key: str) -> str | None:
        return self._producers.get(key) or self._declared_exports.get(key)

    async def get_task_context(self, task_id: str) -> dict[str, Any]:
        """Return variables accessible to task_id (strictly from its ancestor tasks)."""
        async with self._lock:
            allowed_ancestors = self._ancestors.get(task_id, set())
            return {
                k: v
                for k, v in self._values.items()
                if self._producers.get(k) in allowed_ancestors
            }

    async def publish_exports(
        self,
        task_id: str,
        exports: dict[str, tuple[Any, bool]],
    ) -> None:
        """Publish extracted exports from a successfully completed task."""
        async with self._lock:
            for key, (val, is_secret) in exports.items():
                if key in self._values and self._producers.get(key) != task_id:
                    raise DuplicateContextExportError(
                        f"Duplicate context export key '{key}' from task '{task_id}'. "
                        f"Key was already exported by task '{self._producers.get(key)}'."
                    )
                self._values[key] = val
                self._is_secret[key] = is_secret
                self._producers[key] = task_id

    async def get_secret_values(self) -> set[str]:
        """Return string values of all secret-flagged context variables."""
        async with self._lock:
            return {
                str(v)
                for k, v in self._values.items()
                if self._is_secret.get(k, False) and v is not None and str(v).strip()
            }

    def validate_task_expressions(self, task: DAGTaskSchema) -> None:
        """Validate all context expressions in a task before adapter execution."""
        allowed_ancestors = self._ancestors.get(task.task_id, set())

        for step in task.steps:
            self._validate_obj_expressions(
                step.inputs,
                task_id=task.task_id,
                allowed_ancestors=allowed_ancestors,
                context_name="inputs",
            )
            for assertion in step.assertions:
                if assertion.value is not None:
                    self._validate_obj_expressions(
                        assertion.value,
                        task_id=task.task_id,
                        allowed_ancestors=allowed_ancestors,
                        context_name=f"assertion {assertion.type}",
                    )
            if step.action:
                self._validate_obj_expressions(
                    step.action,
                    task_id=task.task_id,
                    allowed_ancestors=allowed_ancestors,
                    context_name="action",
                )

    def _validate_obj_expressions(
        self,
        obj: Any,
        task_id: str,
        allowed_ancestors: set[str],
        context_name: str,
    ) -> None:
        if isinstance(obj, str):
            # Check for unclosed context expressions like '${context...'
            if "${context" in obj and "}" not in obj:
                raise InvalidContextExpressionError(
                    f"Unclosed context expression in task '{task_id}' ({context_name}): '{obj}'"
                )

            # Check candidate template expressions
            for candidate in TEMPLATE_CANDIDATE_REGEX.findall(obj):
                candidate_str = candidate.strip()
                if not candidate_str.startswith("context."):
                    raise InvalidContextExpressionError(
                        f"Unsupported or invalid expression '${{{candidate_str}}}' in task '{task_id}'. "
                        "Only '${context.<var_name>}' expressions are permitted."
                    )

                var_name = candidate_str.split("context.", 1)[1]
                if not var_name or not re.match(r"^[a-zA-Z0-9_]+$", var_name):
                    raise InvalidContextExpressionError(
                        f"Invalid variable name in expression '${{{candidate_str}}}' in task '{task_id}'."
                    )

                # Check disallowed references or missing variables
                if var_name in self._values:
                    producer = self._producers.get(var_name)
                    if producer not in allowed_ancestors:
                        raise DisallowedContextReferenceError(
                            f"Task '{task_id}' cannot access context variable '{var_name}' produced by task '{producer}'. "
                            f"Task '{task_id}' does not depend on '{producer}'."
                        )
                    # Check type mismatch on string interpolation
                    val = self._values[var_name]
                    if isinstance(val, (dict, list)) and obj != f"${{context.{var_name}}}":
                        raise ContextTypeMismatchError(
                            f"Cannot interpolate non-scalar context variable '{var_name}' of type "
                            f"{type(val).__name__} into string in task '{task_id}' ({context_name})."
                        )
                else:
                    # Not yet published in values. Check if declared anywhere.
                    producer = self._declared_exports.get(var_name)
                    if producer is not None:
                        if producer not in allowed_ancestors:
                            raise DisallowedContextReferenceError(
                                f"Task '{task_id}' references context variable '{var_name}' declared by task '{producer}'. "
                                f"Task '{task_id}' does not depend on '{producer}'."
                            )
                        else:
                            raise MissingContextVariableError(
                                f"Context variable '{var_name}' referenced in task '{task_id}' was not published "
                                f"by prerequisite task '{producer}'."
                            )
                    else:
                        raise MissingContextVariableError(
                            f"Missing context variable: '${{context.{var_name}}}' referenced in task '{task_id}' is not defined."
                        )

        elif isinstance(obj, Mapping):
            for v in obj.values():
                self._validate_obj_expressions(v, task_id, allowed_ancestors, context_name)

        elif isinstance(obj, (list, tuple)):
            for item in obj:
                self._validate_obj_expressions(item, task_id, allowed_ancestors, context_name)
