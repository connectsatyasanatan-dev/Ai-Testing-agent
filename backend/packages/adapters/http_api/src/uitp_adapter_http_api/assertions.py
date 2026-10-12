"""
uitp_adapter_http_api.assertions
================================
Deterministic assertion engine for evaluating declarative HTTP assertions against
structured HTTP response payloads (Task P1-05).

Safety and Security Guarantees:
  - Completely deterministic evaluation: no eval, exec, subprocess, or dynamic code.
  - Traversal depth, string lengths, schema sizes, and expression complexities are strictly bounded.
  - Safe error reporting: secrets and sensitive headers (e.g. Set-Cookie) are redacted in failure output.
  - Bounded responses: large bodies are never echoed completely into assertion failure reports.
  - Bounded JSON-path parser supporting dot and array indexing with pathological loop defense.
  - Self-contained JSON Schema Draft 7 validator with strict depth and recursion bounds.
  - Preserves evaluation order and continues evaluation across assertions to provide complete diagnostic reports.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Mapping, Sequence

from uitp_contracts.enums import AssertionType, ErrorCode
from uitp_contracts.plan import (
    AssertionResultSchema,
    AssertionSchema,
    StepAssertionsOutcome,
)

logger = logging.getLogger(__name__)

# Resource and traversal limits
MAX_ASSERTIONS_PER_STEP = 50
MAX_PATH_LENGTH = 256
MAX_TRAVERSAL_DEPTH = 20
MAX_SCHEMA_DEPTH = 15
MAX_SCHEMA_PROPERTIES = 100
MAX_REGEX_LENGTH = 128
MAX_VALUE_REPR_LEN = 160

# Sensitive headers whose values must never be exposed in failure diagnostics
SENSITIVE_HEADERS = frozenset(
    {
        "set-cookie",
        "cookie",
        "authorization",
        "proxy-authorization",
        "x-api-key",
    }
)


def _safe_repr(val: Any) -> str:
    """Safely format values for error messages without unbounded dumps."""
    if val is None:
        return "null"
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, str):
        if len(val) > MAX_VALUE_REPR_LEN:
            return f"'{val[:MAX_VALUE_REPR_LEN]}... [truncated]'"
        return f"'{val}'"
    r = repr(val)
    if len(r) > MAX_VALUE_REPR_LEN:
        return f"{r[:MAX_VALUE_REPR_LEN]}... [truncated]"
    return r


class JsonPathEvaluator:
    """Safe, bounded evaluator for dot-notation and indexed JSON paths."""

    _UNSUPPORTED_OPERATORS = ("..", "*", "?(", "(")

    @classmethod
    def evaluate(cls, data: Any, path: str) -> tuple[bool, Any, str | None]:
        """Extract a value from data using a bounded JSONPath expression.

        Returns:
            (exists: bool, value: Any, error_message: str | None)
        """
        if not path or not isinstance(path, str):
            return False, None, "Path must be a non-empty string"

        path = path.strip()
        if len(path) > MAX_PATH_LENGTH:
            return False, None, f"Path exceeds maximum allowed length of {MAX_PATH_LENGTH} characters"

        for op in cls._UNSUPPORTED_OPERATORS:
            if op in path:
                return (
                    False,
                    None,
                    f"Unsupported JSONPath syntax '{op}'. Only bounded dot and index paths are permitted",
                )

        # Normalize leading $ or $.
        if path == "$":
            return True, data, None
        if path.startswith("$."):
            path = path[2:]
        elif path.startswith("$"):
            path = path[1:]

        tokens = cls._tokenize_path(path)
        if tokens is None:
            return False, None, f"Invalid JSONPath expression syntax: '{path}'"

        if len(tokens) > MAX_TRAVERSAL_DEPTH:
            return False, None, f"Path exceeds maximum traversal depth of {MAX_TRAVERSAL_DEPTH}"

        current = data
        for segment in tokens:
            if isinstance(segment, str):
                if not isinstance(current, Mapping):
                    return False, None, None
                if segment not in current:
                    return False, None, None
                current = current[segment]
            elif isinstance(segment, int):
                if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                    return False, None, None
                if segment < 0 or segment >= len(current):
                    return False, None, None
                current = current[segment]
            else:
                return False, None, "Invalid path token type"

        return True, current, None

    @classmethod
    def _tokenize_path(cls, path: str) -> list[str | int] | None:
        """Tokenize a path like 'user.profile.roles[0].name' into ['user', 'profile', 'roles', 0, 'name']."""
        tokens: list[str | int] = []
        i = 0
        n = len(path)

        while i < n:
            if path[i] == ".":
                i += 1
                continue
            if path[i] == "[":
                end = path.find("]", i)
                if end == -1:
                    return None
                bracket_content = path[i + 1 : end].strip().strip("'\"")
                if bracket_content.isdigit():
                    tokens.append(int(bracket_content))
                elif bracket_content:
                    tokens.append(bracket_content)
                else:
                    return None
                i = end + 1
            else:
                start = i
                next_dot = path.find(".", start)
                next_bracket = path.find("[", start)
                if next_dot == -1 and next_bracket == -1:
                    end = n
                elif next_dot != -1 and (next_bracket == -1 or next_dot < next_bracket):
                    end = next_dot
                else:
                    end = next_bracket

                ident = path[start:end].strip()
                if not ident:
                    return None
                tokens.append(ident)
                i = end

        return tokens


class BoundedJsonSchemaValidator:
    """Safe, bounded JSON Schema validator supporting core Draft 7 / 2020-12 keywords."""

    @classmethod
    def validate(
        cls,
        data: Any,
        schema: Any,
        depth: int = 0,
        path: str = "#",
    ) -> tuple[bool, str | None, str | None]:
        """Validate data against schema.

        Returns:
            (is_valid: bool, error_path: str | None, error_message: str | None)
        """
        if depth > MAX_SCHEMA_DEPTH:
            return False, path, f"Schema recursion depth exceeded limit of {MAX_SCHEMA_DEPTH}"

        if not isinstance(schema, Mapping):
            return False, path, "Schema must be an object mapping"

        if len(schema) > MAX_SCHEMA_PROPERTIES:
            return False, path, f"Schema exceeds limit of {MAX_SCHEMA_PROPERTIES} properties"

        # 1. 'type' keyword
        if "type" in schema:
            expected_type = schema["type"]
            valid_type, type_msg = cls._check_type(data, expected_type)
            if not valid_type:
                return False, path, type_msg

        # 2. 'enum' keyword
        if "enum" in schema:
            enum_values = schema["enum"]
            if not isinstance(enum_values, Sequence):
                return False, path, "'enum' schema keyword must be a list"
            if data not in enum_values:
                return False, path, f"Value {data} is not in enum {enum_values}"

        # 3. Numeric constraints
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            if "minimum" in schema and data < schema["minimum"]:
                return False, path, f"Value {data} is less than minimum {schema['minimum']}"
            if "maximum" in schema and data > schema["maximum"]:
                return False, path, f"Value {data} is greater than maximum {schema['maximum']}"

        # 4. String constraints
        if isinstance(data, str):
            if "minLength" in schema and len(data) < schema["minLength"]:
                return False, path, f"String length {len(data)} is less than minLength {schema['minLength']}"
            if "maxLength" in schema and len(data) > schema["maxLength"]:
                return False, path, f"String length {len(data)} is greater than maxLength {schema['maxLength']}"
            if "pattern" in schema:
                pattern = schema["pattern"]
                if len(pattern) > MAX_REGEX_LENGTH:
                    return False, path, "Pattern regex exceeds maximum length"
                try:
                    if not re.search(pattern, data):
                        return False, path, f"String does not match pattern '{pattern}'"
                except re.error as e:
                    return False, path, f"Invalid pattern regex: {e}"

        # 5. Object constraints
        if isinstance(data, Mapping):
            if "required" in schema:
                required_props = schema["required"]
                if isinstance(required_props, Sequence):
                    for prop in required_props:
                        if prop not in data:
                            return False, f"{path}/{prop}", f"Missing required property '{prop}'"

            if "properties" in schema and isinstance(schema["properties"], Mapping):
                for prop, prop_schema in schema["properties"].items():
                    if prop in data:
                        ok, err_path, err_msg = cls.validate(
                            data[prop], prop_schema, depth=depth + 1, path=f"{path}/{prop}"
                        )
                        if not ok:
                            return False, err_path, err_msg

            if "additionalProperties" in schema and schema["additionalProperties"] is False:
                allowed_props = set(schema.get("properties", {}).keys())
                for prop in data.keys():
                    if prop not in allowed_props:
                        return False, f"{path}/{prop}", f"Unexpected additional property '{prop}'"

        # 6. Array constraints
        if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
            if "items" in schema and isinstance(schema["items"], Mapping):
                item_schema = schema["items"]
                for idx, item in enumerate(data):
                    ok, err_path, err_msg = cls.validate(
                        item, item_schema, depth=depth + 1, path=f"{path}[{idx}]"
                    )
                    if not ok:
                        return False, err_path, err_msg

        return True, None, None

    @classmethod
    def _check_type(cls, data: Any, expected: Any) -> tuple[bool, str]:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, (list, tuple)):
            return False, f"Invalid 'type' specification in schema: {expected}"

        for t in expected_types:
            if t == "string" and isinstance(data, str):
                return True, ""
            if t == "integer" and isinstance(data, int) and not isinstance(data, bool):
                return True, ""
            if t == "number" and isinstance(data, (int, float)) and not isinstance(data, bool):
                return True, ""
            if t == "boolean" and isinstance(data, bool):
                return True, ""
            if t == "array" and isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
                return True, ""
            if t == "object" and isinstance(data, Mapping):
                return True, ""
            if t == "null" and data is None:
                return True, ""

        actual_t = type(data).__name__
        return False, f"Expected type '{expected}', got '{actual_t}'"


class HttpAssertionEngine:
    """Evaluates declarative assertions against structured HTTP execution outputs."""

    def evaluate(
        self,
        response_outputs: Mapping[str, Any],
        assertions: Sequence[AssertionSchema],
    ) -> StepAssertionsOutcome:
        """Evaluate a collection of assertions against an HTTP response.

        Guarantees:
          - Evaluates all assertions deterministically in original order.
          - Never raises unhandled exceptions.
          - Aggregates pass/fail counts and failure reasons.
        """
        results: list[AssertionResultSchema] = []
        failure_reasons: list[str] = []

        if len(assertions) > MAX_ASSERTIONS_PER_STEP:
            msg = f"Step assertion count ({len(assertions)}) exceeds maximum of {MAX_ASSERTIONS_PER_STEP}"
            return StepAssertionsOutcome(
                passed=False,
                total_assertions=len(assertions),
                passed_count=0,
                failed_count=len(assertions),
                results=[
                    AssertionResultSchema(
                        type="count_limit",
                        passed=False,
                        message=msg,
                        error_code=ErrorCode.VALIDATION_ERROR,
                    )
                ],
                failure_reasons=[msg],
            )

        for assertion in assertions:
            start_t = time.monotonic()
            try:
                res = self._evaluate_single(response_outputs, assertion)
            except Exception as exc:
                logger.exception("Unexpected exception in assertion evaluator: %s", exc)
                res = AssertionResultSchema(
                    type=assertion.type,
                    passed=False,
                    message=f"Internal evaluator failure: {type(exc).__name__}",
                    error_code=ErrorCode.VALIDATION_ERROR,
                )

            duration_ms = (time.monotonic() - start_t) * 1000
            res = res.model_copy(update={"duration_ms": duration_ms})
            results.append(res)

            if not res.passed:
                reason = res.message or f"Assertion {res.type} failed"
                if assertion.message:
                    reason = f"{assertion.message}: {reason}"
                failure_reasons.append(reason)

        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count
        overall_passed = failed_count == 0

        return StepAssertionsOutcome(
            passed=overall_passed,
            total_assertions=len(assertions),
            passed_count=passed_count,
            failed_count=failed_count,
            results=results,
            failure_reasons=failure_reasons,
        )

    def _evaluate_single(
        self,
        outputs: Mapping[str, Any],
        assertion: AssertionSchema,
    ) -> AssertionResultSchema:
        atype = assertion.type

        if atype == AssertionType.STATUS_CODE:
            return self._eval_status_code(outputs, assertion)
        elif atype == AssertionType.JSON_PATH_EQUALS:
            return self._eval_json_path_equals(outputs, assertion)
        elif atype == AssertionType.JSON_PATH_EXISTS:
            return self._eval_json_path_exists(outputs, assertion)
        elif atype == AssertionType.JSON_PATH_MATCHES:
            return self._eval_json_path_matches(outputs, assertion)
        elif atype == AssertionType.HEADER_EQUALS:
            return self._eval_header_equals(outputs, assertion)
        elif atype == AssertionType.HEADER_EXISTS:
            return self._eval_header_exists(outputs, assertion)
        elif atype == AssertionType.JSON_SCHEMA:
            return self._eval_json_schema(outputs, assertion)
        elif atype == AssertionType.RESPONSE_TIME_UNDER:
            return self._eval_response_time_under(outputs, assertion)
        else:
            return AssertionResultSchema(
                type=atype,
                passed=False,
                error_code=ErrorCode.UNSUPPORTED_ASSERTION,
                message=f"Assertion type '{atype}' is unsupported by the HTTP API adapter",
            )

    def _eval_status_code(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        try:
            expected = int(assertion.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="Expected status code must be a valid integer",
                expected=assertion.value,
            )

        if not (100 <= expected <= 599):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="Expected status code must be between 100 and 599",
                expected=expected,
            )

        actual = outputs.get("status_code")
        if actual == expected:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=actual,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=expected,
            actual=actual,
            message=f"Expected status code {expected}, got {actual}",
        )

    def _eval_json_path_equals(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        path = assertion.path
        if not path:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="json_path_equals requires a 'path' expression",
            )

        body = outputs.get("body")
        exists, actual, err = JsonPathEvaluator.evaluate(body, path)
        if err:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=err,
            )

        if not exists:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"JSON path '{path}' does not exist in response body",
                expected=assertion.value,
            )

        if actual == assertion.value:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=path,
                expected=assertion.value,
                actual=actual,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=path,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=assertion.value,
            actual=actual,
            message=f"JSON path '{path}' value mismatch: expected {_safe_repr(assertion.value)}, got {_safe_repr(actual)}",
        )

    def _eval_json_path_exists(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        path = assertion.path
        if not path:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="json_path_exists requires a 'path' expression",
            )

        body = outputs.get("body")
        exists, actual, err = JsonPathEvaluator.evaluate(body, path)
        if err:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=err,
            )

        if exists:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=path,
                actual=actual,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=path,
            error_code=ErrorCode.ASSERTION_FAILED,
            message=f"JSON path '{path}' does not exist in response body",
        )

    def _eval_json_path_matches(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        path = assertion.path
        pattern = str(assertion.value or "")
        if not path:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="json_path_matches requires a 'path' expression",
            )
        if not pattern or len(pattern) > MAX_REGEX_LENGTH:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=f"json_path_matches requires a pattern string <= {MAX_REGEX_LENGTH} characters",
            )

        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=f"Invalid regular expression pattern: {e}",
            )

        body = outputs.get("body")
        exists, actual, err = JsonPathEvaluator.evaluate(body, path)
        if err:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=err,
            )

        if not exists:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"JSON path '{path}' not found in response body",
            )

        if not isinstance(actual, (str, int, float, bool)):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=path,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"JSON path '{path}' value is not a scalar (type: {type(actual).__name__})",
            )

        if compiled.search(str(actual)):
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=path,
                expected=pattern,
                actual=actual,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=path,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=pattern,
            actual=actual,
            message=f"JSON path '{path}' value {_safe_repr(actual)} does not match pattern '{pattern}'",
        )

    def _eval_header_equals(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        header_name = assertion.path or str(assertion.value or "").split(":", 1)[0]
        header_name = header_name.strip()
        expected_val = str(assertion.value or "") if assertion.path else ""
        if not assertion.path and ":" in str(assertion.value or ""):
            expected_val = str(assertion.value or "").split(":", 1)[1].strip()

        if not header_name:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="header_equals requires header name in 'path'",
            )

        headers = outputs.get("headers") or {}
        # Case-insensitive header matching
        actual_val: str | None = None
        for k, v in headers.items():
            if k.lower() == header_name.lower():
                actual_val = str(v)
                break

        is_sensitive = header_name.lower() in SENSITIVE_HEADERS
        safe_expected = "<redacted>" if is_sensitive else expected_val
        safe_actual = "<redacted>" if is_sensitive else actual_val

        if actual_val is None:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=header_name,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"Response header '{header_name}' was not found",
            )

        if actual_val == expected_val:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=header_name,
                expected=safe_expected,
                actual=safe_actual,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=header_name,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=safe_expected,
            actual=safe_actual,
            message=f"Header '{header_name}' mismatch: expected '{safe_expected}', got '{safe_actual}'",
        )

    def _eval_header_exists(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        header_name = (assertion.path or str(assertion.value or "")).strip()
        if not header_name:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="header_exists requires header name in 'path' or 'value'",
            )

        headers = outputs.get("headers") or {}
        found = any(k.lower() == header_name.lower() for k in headers.keys())

        if found:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=header_name,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=header_name,
            error_code=ErrorCode.ASSERTION_FAILED,
            message=f"Required header '{header_name}' was not present in response headers",
        )

    def _eval_json_schema(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        schema = assertion.schema_
        if not schema or not isinstance(schema, Mapping):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="json_schema assertion requires a valid JSON schema dictionary in 'schema'",
            )

        body = outputs.get("body")
        if body is None or not isinstance(body, (Mapping, Sequence)) or isinstance(body, (str, bytes)):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.ASSERTION_FAILED,
                message="Response body is not a parsed JSON object or array",
            )

        valid, err_path, err_msg = BoundedJsonSchemaValidator.validate(body, schema)
        if valid:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            path=err_path,
            error_code=ErrorCode.ASSERTION_FAILED,
            message=f"JSON schema validation failed at {err_path}: {err_msg}",
        )

    def _eval_response_time_under(
        self, outputs: Mapping[str, Any], assertion: AssertionSchema
    ) -> AssertionResultSchema:
        try:
            threshold = float(assertion.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="response_time_under requires a numeric threshold in milliseconds",
            )

        if threshold <= 0:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="response_time_under threshold must be greater than 0 ms",
            )

        duration_ms = float(outputs.get("duration_ms", 0.0))
        if duration_ms <= threshold:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=threshold,
                actual=duration_ms,
            )

        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=threshold,
            actual=duration_ms,
            message=f"Response duration {duration_ms:.1f}ms exceeded threshold {threshold:.1f}ms",
        )
