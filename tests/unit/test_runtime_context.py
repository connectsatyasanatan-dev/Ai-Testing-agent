"""
tests/unit/test_runtime_context.py
==================================
Unit tests for run-scoped RuntimeContext:
- Context isolation between runs and tenants.
- Transitive ancestor scoping and disallowed reference rejection.
- Missing variable and malformed expression detection.
- Type mismatch validation on non-scalar interpolation.
- Missing and null export detection with structured error diagnostics.
- Duplicate export key conflict rejection.
- Secret redaction utilities.
- Concurrency safety under concurrent task publication.
"""
from __future__ import annotations

import asyncio
import pytest

from uitp_contracts.enums import AdapterType, AgentRole
from uitp_contracts.plan import AssertionSchema, ContextExportDef, DAGTaskSchema, TestStepSchema
from uitp_orchestrator.context import (
    RuntimeContext,
    extract_context_exports,
    extract_task_exports,
    redact_sensitive_values,
    resolve_context_expressions,
)
from uitp_orchestrator.exceptions import (
    ContextTypeMismatchError,
    DisallowedContextReferenceError,
    DuplicateContextExportError,
    InvalidContextExpressionError,
    MissingContextExportError,
    MissingContextVariableError,
)


def _make_task(
    task_id: str,
    depends_on: list[str] | None = None,
    exports: dict[str, ContextExportDef] | None = None,
    steps: list[TestStepSchema] | None = None,
) -> DAGTaskSchema:
    return DAGTaskSchema(
        task_id=task_id,
        name=f"Task {task_id}",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        depends_on=depends_on or [],
        exports=exports or {},
        steps=steps or [],
    )


def test_runtime_context_isolation_between_runs() -> None:
    """Distinct runs maintain separate in-memory state."""
    t1 = _make_task("t1", exports={"tok": ContextExportDef(json_path="$.tok")})
    ctx_run_a = RuntimeContext("run_a", [t1])
    ctx_run_b = RuntimeContext("run_b", [t1])

    async def _test() -> None:
        await ctx_run_a.publish_exports("t1", {"tok": ("token_a", False)})
        await ctx_run_b.publish_exports("t1", {"tok": ("token_b", False)})

        # Each run only sees its own value
        assert ctx_run_a._values["tok"] == "token_a"
        assert ctx_run_b._values["tok"] == "token_b"

    asyncio.run(_test())


def test_ancestor_scoping_disallows_independent_task_references() -> None:
    """Task cannot consume context from an independent task it does not depend on."""
    # ta -> tb; tc is independent of ta
    ta = _make_task("ta", exports={"order_id": ContextExportDef(json_path="$.id")})
    tb = _make_task("tb", depends_on=["ta"])
    tc = _make_task("tc", depends_on=[], steps=[
        TestStepSchema(
            step_id="s1",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/orders/${context.order_id}"},
        )
    ])

    ctx = RuntimeContext("run_1", [ta, tb, tc])

    async def _test() -> None:
        await ctx.publish_exports("ta", {"order_id": ("ord_123", False)})

        # tb depends on ta -> allowed
        tb_ctx = await ctx.get_task_context("tb")
        assert tb_ctx.get("order_id") == "ord_123"

        # tc does not depend on ta -> context is empty for tc
        tc_ctx = await ctx.get_task_context("tc")
        assert "order_id" not in tc_ctx

        # Validating tc raises DisallowedContextReferenceError
        with pytest.raises(DisallowedContextReferenceError) as exc_info:
            ctx.validate_task_expressions(tc)
        assert "cannot access context variable 'order_id' produced by task 'ta'" in str(exc_info.value)

    asyncio.run(_test())


def test_missing_variable_validation_error() -> None:
    """Referencing an undeclared context variable raises MissingContextVariableError."""
    t = _make_task("t_sample", steps=[
        TestStepSchema(
            step_id="s1",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"headers": {"X-Token": "${context.nonexistent_var}"}},
        )
    ])
    ctx = RuntimeContext("run_1", [t])

    with pytest.raises(MissingContextVariableError) as exc_info:
        ctx.validate_task_expressions(t)
    assert "nonexistent_var" in str(exc_info.value)



def test_invalid_expression_syntax() -> None:
    """Malformed or non-context expressions raise InvalidContextExpressionError."""
    t1 = _make_task("t1", steps=[
        TestStepSchema(
            step_id="s1",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/test/${context.}"},
        )
    ])
    ctx = RuntimeContext("run_1", [t1])
    with pytest.raises(InvalidContextExpressionError):
        ctx.validate_task_expressions(t1)

    t2 = _make_task("t2", steps=[
        TestStepSchema(
            step_id="s2",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/test/${env.secret}"},
        )
    ])
    with pytest.raises(InvalidContextExpressionError):
        ctx.validate_task_expressions(t2)

    t3 = _make_task("t3", steps=[
        TestStepSchema(
            step_id="s3",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/test/${context.unclosed"},
        )
    ])
    with pytest.raises(InvalidContextExpressionError):
        ctx.validate_task_expressions(t3)


def test_context_type_mismatch_string_interpolation() -> None:
    """Interpolating a dictionary or list into a string template raises ContextTypeMismatchError."""
    ta = _make_task("ta", exports={"details": ContextExportDef(json_path="$.details")})
    tb = _make_task("tb", depends_on=["ta"], steps=[
        TestStepSchema(
            step_id="s1",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/profile?info=${context.details}"},
        )
    ])
    ctx = RuntimeContext("run_1", [ta, tb])

    async def _test() -> None:
        # Publish complex dict object
        await ctx.publish_exports("ta", {"details": ({"k": "v"}, False)})
        with pytest.raises(ContextTypeMismatchError):
            ctx.validate_task_expressions(tb)

    asyncio.run(_test())


def test_missing_and_null_export_fails_with_structured_error() -> None:
    """Task declaring export fails if JSONPath is not present or evaluates to null."""
    t = _make_task("t_export", exports={
        "token": ContextExportDef(json_path="$.auth.token"),
    })


    # Missing field
    step_outputs = [{"status_code": 200, "body": {"user": "alice"}}]
    with pytest.raises(MissingContextExportError) as exc_info:
        extract_task_exports(t, step_outputs)
    assert "token" in str(exc_info.value)
    assert "$.auth.token" in str(exc_info.value)

    # Null field
    step_outputs_null = [{"status_code": 200, "body": {"auth": {"token": None}}}]
    with pytest.raises(MissingContextExportError) as exc_null:
        extract_task_exports(t, step_outputs_null)
    assert "missing or null" in str(exc_null.value)


def test_duplicate_export_declarations_rejected() -> None:
    """Two tasks declaring identical export keys raises DuplicateContextExportError."""
    t1 = _make_task("t1", exports={"same_key": ContextExportDef(json_path="$.a")})
    t2 = _make_task("t2", exports={"same_key": ContextExportDef(json_path="$.b")})

    with pytest.raises(DuplicateContextExportError) as exc_info:
        RuntimeContext("run_1", [t1, t2])
    assert "Duplicate declared export key 'same_key'" in str(exc_info.value)


def test_duplicate_export_runtime_overwrite_rejected() -> None:
    """Task attempting to overwrite an already-published key from another task raises DuplicateContextExportError."""
    t1 = _make_task("t1", exports={"shared": ContextExportDef(json_path="$.val")})
    t2 = _make_task("t2", exports={"other": ContextExportDef(json_path="$.val")})

    ctx = RuntimeContext("run_1", [t1, t2])

    async def _test() -> None:
        await ctx.publish_exports("t1", {"shared": ("val1", False)})
        with pytest.raises(DuplicateContextExportError):
            # t2 tries to publish key 'shared'
            await ctx.publish_exports("t2", {"shared": ("val2", False)})

    asyncio.run(_test())


def test_secret_redaction_helper() -> None:
    """redact_sensitive_values sanitizes known secret strings across nested objects."""
    secrets = {"secret-token-xyz-12345", "super_password_99"}
    payload = {
        "url": "https://api.test?key=secret-token-xyz-12345",
        "headers": {
            "Authorization": "Bearer secret-token-xyz-12345",
            "X-Pass": "super_password_99",
        },
        "items": ["safe_val", "bearer secret-token-xyz-12345"],
    }
    redacted = redact_sensitive_values(payload, secrets)

    assert "secret-token-xyz-12345" not in str(redacted)
    assert "super_password_99" not in str(redacted)
    assert redacted["url"] == "https://api.test?key=[REDACTED]"
    assert redacted["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert redacted["headers"]["X-Pass"] == "[REDACTED]"
    assert redacted["items"][1] == "bearer [REDACTED]"


def test_concurrency_safety_in_runtime_context() -> None:
    """Multiple concurrent coroutines publishing exports do not corrupt context."""
    tasks = [_make_task(f"t_{i}", exports={f"k_{i}": ContextExportDef(json_path="$.v")}) for i in range(10)]
    ctx = RuntimeContext("run_concurrent", tasks)

    async def _publisher(i: int) -> None:
        await asyncio.sleep(0.01)
        await ctx.publish_exports(f"t_{i}", {f"k_{i}": (f"value_{i}", i % 2 == 0)})

    async def _runner() -> None:
        await asyncio.gather(*[_publisher(i) for i in range(10)])
        assert len(ctx._values) == 10
        for i in range(10):
            assert ctx._values[f"k_{i}"] == f"value_{i}"

    asyncio.run(_runner())
