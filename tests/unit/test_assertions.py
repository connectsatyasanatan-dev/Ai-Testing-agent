"""
tests/unit/test_assertions.py
=============================
Unit tests for the Deterministic HTTP Assertion Engine (Task P1-05).

Verifies:
  1. Matching and mismatching HTTP statuses.
  2. Matching and missing JSON-path values.
  3. Null values, falsy values, and type mismatches in JSON-path.
  4. Array indexing and rejection of unsupported JSON-path syntax.
  5. Present and missing HTTP headers.
  6. Case-insensitivity in HTTP header matching.
  7. Valid and invalid JSON Schema evaluation.
  8. Nested JSON Schema mismatches reporting concise paths.
  9. Response-time pass and fail using controlled elapsed values.
  10. Invalid assertion definitions (e.g. non-numeric status, missing path).
  11. Unsupported assertion types (web actions or unknown types).
  12. Resource limits: max assertions count, path length, recursion depth.
  13. Safe error formatting and sensitive header redaction (Set-Cookie).
  14. Deterministic aggregation when multiple assertions pass or fail.
  15. Malformed response data handling (non-JSON body, missing fields).
  16. Defense against arbitrary code execution in all fields.
  17. End-to-end integration with HttpApiAdapter.execute_step.
"""
from __future__ import annotations

import pytest

from uitp_adapter_base.base import ExecutionContext
from uitp_adapter_http_api import (
    BoundedJsonSchemaValidator,
    HttpApiAdapter,
    HttpAssertionEngine,
    JsonPathEvaluator,
)
from uitp_contracts.enums import (
    AdapterType,
    AssertionType,
    ErrorCode,
    StepStatus,
)
from uitp_contracts.plan import AssertionSchema, TestStepSchema


@pytest.fixture
def engine():
    return HttpAssertionEngine()


@pytest.fixture
def sample_response():
    return {
        "status_code": 200,
        "headers": {
            "Content-Type": "application/json",
            "X-Request-Id": "req-12345",
            "Set-Cookie": "session_secret_token=abc123xyz",
        },
        "body": {
            "id": 42,
            "name": "Deterministic Widget",
            "active": True,
            "tags": ["alpha", "beta"],
            "owner": None,
            "metadata": {
                "version": "1.0.0",
                "score": 98.5,
            },
        },
        "duration_ms": 125.0,
        "url": "http://api.local/items/42",
    }


@pytest.mark.unit
class TestStatusCodeAssertions:
    def test_status_code_match(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.STATUS_CODE, value=200)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True
        assert outcome.total_assertions == 1
        assert outcome.passed_count == 1
        assert outcome.results[0].passed is True

    def test_status_code_mismatch(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.STATUS_CODE, value=404)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.failed_count == 1
        assert outcome.results[0].passed is False
        assert outcome.results[0].error_code == ErrorCode.ASSERTION_FAILED
        assert "Expected status code 404, got 200" in (outcome.results[0].message or "")

    def test_invalid_status_code_definition(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.STATUS_CODE, value="not_an_int")
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].error_code == ErrorCode.VALIDATION_ERROR

    def test_out_of_range_status_code(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.STATUS_CODE, value=999)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].error_code == ErrorCode.VALIDATION_ERROR


@pytest.mark.unit
class TestJsonPathAssertions:
    def test_json_path_equals_success(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EQUALS,
            path="$.metadata.version",
            value="1.0.0",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True
        assert outcome.results[0].actual == "1.0.0"

    def test_json_path_equals_mismatch(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EQUALS,
            path="$.id",
            value=999,
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].passed is False
        assert "value mismatch" in (outcome.results[0].message or "")

    def test_json_path_exists_success(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EXISTS,
            path="$.tags[1]",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True
        assert outcome.results[0].actual == "beta"

    def test_json_path_exists_for_null_field(self, engine: HttpAssertionEngine, sample_response) -> None:
        # Field exists even though value is null/None
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EXISTS,
            path="$.owner",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_json_path_missing_field(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EXISTS,
            path="$.non_existent_key",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "does not exist" in (outcome.results[0].message or "")

    def test_json_path_array_indexing(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EQUALS,
            path="tags[0]",
            value="alpha",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_json_path_array_index_out_of_bounds(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_EXISTS,
            path="$.tags[99]",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False

    def test_unsupported_json_path_operators_rejected(self) -> None:
        # Recursive descent and wildcards are strictly rejected
        exists, _, err1 = JsonPathEvaluator.evaluate({"a": 1}, "$..a")
        assert exists is False
        assert "Unsupported JSONPath syntax '..'" in (err1 or "")

        exists, _, err2 = JsonPathEvaluator.evaluate({"a": 1}, "$.*")
        assert exists is False
        assert "Unsupported JSONPath syntax '*'" in (err2 or "")

        exists, _, err3 = JsonPathEvaluator.evaluate({"a": 1}, "$[?(@.id==1)]")
        assert exists is False
        assert "Unsupported JSONPath syntax '?('" in (err3 or "")

    def test_json_path_matches_regex_success(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_MATCHES,
            path="$.metadata.version",
            value=r"^\d+\.\d+\.\d+$",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_json_path_matches_regex_mismatch(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.JSON_PATH_MATCHES,
            path="$.name",
            value=r"^Wrong.*$",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "does not match pattern" in (outcome.results[0].message or "")


@pytest.mark.unit
class TestHeaderAssertions:
    def test_header_equals_success_case_insensitive(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.HEADER_EQUALS,
            path="content-type",  # lower case
            value="application/json",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_header_equals_mismatch(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.HEADER_EQUALS,
            path="Content-Type",
            value="text/html",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "mismatch" in (outcome.results[0].message or "")

    def test_header_exists_success(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.HEADER_EXISTS,
            path="x-request-id",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_header_exists_absent(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(
            type=AssertionType.HEADER_EXISTS,
            path="x-missing-header",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "was not present" in (outcome.results[0].message or "")

    def test_sensitive_header_redaction_in_failure_message(self, engine: HttpAssertionEngine, sample_response) -> None:
        # Mismatch on Set-Cookie must redact actual value
        assertion = AssertionSchema(
            type=AssertionType.HEADER_EQUALS,
            path="Set-Cookie",
            value="expected_cookie",
        )
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        # Neither expected nor actual cookie secrets must be leaked
        assert "session_secret_token" not in (outcome.results[0].message or "")
        assert "<redacted>" in (outcome.results[0].message or "")


@pytest.mark.unit
class TestJsonSchemaAssertions:
    def test_valid_json_schema(self, engine: HttpAssertionEngine, sample_response) -> None:
        schema = {
            "type": "object",
            "required": ["id", "name", "active", "tags"],
            "properties": {
                "id": {"type": "integer", "minimum": 1},
                "name": {"type": "string"},
                "active": {"type": "boolean"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        assertion = AssertionSchema(type=AssertionType.JSON_SCHEMA, schema=schema)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_json_schema_missing_required_property(self, engine: HttpAssertionEngine, sample_response) -> None:
        schema = {
            "type": "object",
            "required": ["id", "required_missing_field"],
        }
        assertion = AssertionSchema(type=AssertionType.JSON_SCHEMA, schema=schema)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "Missing required property 'required_missing_field'" in (outcome.results[0].message or "")

    def test_json_schema_nested_type_mismatch(self, engine: HttpAssertionEngine, sample_response) -> None:
        schema = {
            "type": "object",
            "properties": {
                "metadata": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "string"},  # score is a float 98.5
                    },
                }
            },
        }
        assertion = AssertionSchema(type=AssertionType.JSON_SCHEMA, schema=schema)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "#/metadata/score" in (outcome.results[0].path or "")
        assert "Expected type 'string'" in (outcome.results[0].message or "")

    def test_schema_depth_limit_defense(self) -> None:
        # Build deeply recursive schema and data
        curr_schema = {"type": "string"}
        curr_data: Any = "val"
        for _ in range(25):
            curr_schema = {"type": "object", "properties": {"nested": curr_schema}}
            curr_data = {"nested": curr_data}
        ok, _, err = BoundedJsonSchemaValidator.validate(curr_data, curr_schema)
        assert ok is False
        assert "depth" in (err or "").lower()


@pytest.mark.unit
class TestResponseTimeAssertions:
    def test_response_time_under_threshold(self, engine: HttpAssertionEngine, sample_response) -> None:
        # duration_ms is 125.0
        assertion = AssertionSchema(type=AssertionType.RESPONSE_TIME_UNDER, value=500.0)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is True

    def test_response_time_exceeded(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.RESPONSE_TIME_UNDER, value=50.0)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert "125.0ms exceeded threshold 50.0ms" in (outcome.results[0].message or "")

    def test_response_time_invalid_value(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertion = AssertionSchema(type=AssertionType.RESPONSE_TIME_UNDER, value=-10.0)
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].error_code == ErrorCode.VALIDATION_ERROR


@pytest.mark.unit
class TestAssertionAggregationAndSafety:
    def test_aggregation_all_passing(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertions = [
            AssertionSchema(type=AssertionType.STATUS_CODE, value=200),
            AssertionSchema(type=AssertionType.HEADER_EXISTS, path="Content-Type"),
            AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path="$.id", value=42),
        ]
        outcome = engine.evaluate(sample_response, assertions)
        assert outcome.passed is True
        assert outcome.total_assertions == 3
        assert outcome.passed_count == 3
        assert outcome.failed_count == 0
        assert len(outcome.failure_reasons) == 0

    def test_aggregation_one_failing_makes_outcome_fail(self, engine: HttpAssertionEngine, sample_response) -> None:
        assertions = [
            AssertionSchema(type=AssertionType.STATUS_CODE, value=200),
            AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path="$.id", value=999),  # fails
            AssertionSchema(type=AssertionType.HEADER_EXISTS, path="Content-Type"),
        ]
        outcome = engine.evaluate(sample_response, assertions)
        assert outcome.passed is False
        assert outcome.total_assertions == 3
        assert outcome.passed_count == 2
        assert outcome.failed_count == 1
        assert len(outcome.failure_reasons) == 1
        # Order is preserved
        assert outcome.results[0].passed is True
        assert outcome.results[1].passed is False
        assert outcome.results[2].passed is True

    def test_unsupported_web_assertion_type_handled_gracefully(
        self, engine: HttpAssertionEngine, sample_response
    ) -> None:
        assertion = AssertionSchema(type=AssertionType.TEXT_VISIBLE, value="Login")
        outcome = engine.evaluate(sample_response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].error_code == ErrorCode.UNSUPPORTED_ASSERTION
        assert "unsupported by the HTTP API adapter" in (outcome.results[0].message or "")

    def test_assertion_count_limit_defense(self, engine: HttpAssertionEngine, sample_response) -> None:
        # Create 60 assertions (limit is 50)
        assertions = [
            AssertionSchema(type=AssertionType.STATUS_CODE, value=200)
            for _ in range(60)
        ]
        outcome = engine.evaluate(sample_response, assertions)
        assert outcome.passed is False
        assert "exceeds maximum of 50" in outcome.failure_reasons[0]

    def test_non_json_body_handled_safely(self, engine: HttpAssertionEngine) -> None:
        response = {
            "status_code": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": "Raw non-JSON string response",
            "duration_ms": 10.0,
        }
        assertion = AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path="$.foo", value=1)
        outcome = engine.evaluate(response, [assertion])
        assert outcome.passed is False
        assert outcome.results[0].passed is False

    def test_no_arbitrary_code_execution_under_attack_payloads(
        self, engine: HttpAssertionEngine, sample_response
    ) -> None:
        malicious_paths = [
            "__import__('os').system('ls')",
            "exec('import sys')",
            "eval('1+1')",
            "constructor.prototype",
            "; DROP TABLE users; --",
        ]
        for path in malicious_paths:
            assertion = AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path=path, value="x")
            outcome = engine.evaluate(sample_response, [assertion])
            assert outcome.passed is False
            assert outcome.results[0].passed is False


@pytest.mark.unit
class TestHttpApiAdapterAssertionIntegration:
    @pytest.mark.asyncio
    async def test_adapter_execute_step_with_passing_assertions(self) -> None:
        # Custom dummy adapter with predefined HTTP response to avoid network sockets
        adapter = HttpApiAdapter()
        # Mock _execute_http_attempt directly for deterministic assertion test
        from uitp_adapter_base.base import StepExecutionResult

        dummy_outputs = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"status": "ok", "count": 10},
            "duration_ms": 25.0,
            "attempt_count": 1,
            "url": "http://mock-api.local/ok",
            "redirect_count": 0,
        }
        async def fake_execute(*args, **kwargs):
            return StepExecutionResult(status=StepStatus.PASSED, outputs=dummy_outputs)

        adapter._execute_http_attempt = fake_execute  # type: ignore[assignment]
        adapter.ssrf_validator.validate_url = lambda url, allowlist=None: None  # type: ignore[assignment]

        step = TestStepSchema(
            step_id="step_assert_pass",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok"},
            assertions=[
                AssertionSchema(type=AssertionType.STATUS_CODE, value=200),
                AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path="$.count", value=10),
            ],
        )
        ctx = ExecutionContext(run_id="r1", step_id="s1", base_url="http://mock-api.local")
        res = await adapter.execute_step(step, ctx)

        assert res.status == StepStatus.PASSED
        assert res.outputs["assertions"]["passed"] is True
        assert res.outputs["assertions"]["passed_count"] == 2

    @pytest.mark.asyncio
    async def test_adapter_execute_step_with_failing_assertions_fails_step(self) -> None:
        adapter = HttpApiAdapter()
        from uitp_adapter_base.base import StepExecutionResult

        dummy_outputs = {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": {"status": "ok", "count": 10},
            "duration_ms": 25.0,
            "attempt_count": 1,
            "url": "http://mock-api.local/ok",
            "redirect_count": 0,
        }
        async def fake_execute(*args, **kwargs):
            return StepExecutionResult(status=StepStatus.PASSED, outputs=dummy_outputs)

        adapter._execute_http_attempt = fake_execute  # type: ignore[assignment]
        adapter.ssrf_validator.validate_url = lambda url, allowlist=None: None  # type: ignore[assignment]

        step = TestStepSchema(
            step_id="step_assert_fail",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok"},
            assertions=[
                AssertionSchema(type=AssertionType.STATUS_CODE, value=200),
                # This assertion fails:
                AssertionSchema(type=AssertionType.JSON_PATH_EQUALS, path="$.count", value=999),
            ],
        )
        ctx = ExecutionContext(run_id="r1", step_id="s1", base_url="http://mock-api.local")
        res = await adapter.execute_step(step, ctx)

        assert res.status == StepStatus.FAILED
        assert res.error_code == ErrorCode.ASSERTION_FAILED
        assert res.outputs["assertions"]["passed"] is False
        assert res.outputs["assertions"]["failed_count"] == 1
        assert "value mismatch" in (res.error_message or "")
