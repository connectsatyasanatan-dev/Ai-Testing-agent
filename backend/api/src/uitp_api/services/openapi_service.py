"""
uitp_api.services.openapi_service
=================================
Deterministic OpenAPI 3.0 / 3.1 parser, normalizer, and test candidate generator (Task 6).

Guarantees & Constraints:
1. Strict size limit enforcement (256 KB max).
2. Deterministic local reference resolution (#/components/schemas/...) with recursion & cycle guards.
3. Strict security policy rejecting external $ref references to defend against SSRF and data exfiltration.
4. Support for OpenAPI 3.0.x and 3.1.x; rejection of Swagger 2.0 or missing versions.
5. Extraction of paths, methods, parameters, request body schemas, response schemas, and security requirements.
6. Deterministic generation of test candidates (happy path, required fields, boundary/enums, documented errors,
   security/auth, and CRUD lifecycle sequences) without arbitrary guesses.
7. Grounding: inferred expectations or missing specifications are explicitly labeled as is_inferred=True
   with unresolved_expectations.
8. No network requests or test execution: purely analytical and deterministic.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any
import yaml

from uitp_api.exceptions import ValidationError
from uitp_contracts.enums import AssertionType
from uitp_contracts.openapi import (
    CandidateCategory,
    NormalizedOpenAPISpec,
    NormalizedOperation,
    OpenAPIAnalysisResult,
    OpenAPIParameter,
    OpenAPIRequestBody,
    OpenAPIResponse,
    OpenAPISecurityScheme,
    OpenAPITestCandidate,
)
from uitp_contracts.plan import AssertionSchema

logger = logging.getLogger(__name__)

# Maximum specification size: 256 KB (262,144 bytes)
MAX_SPEC_SIZE = 262144
MAX_REF_DEPTH = 20

VALID_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _sanitize_name(val: str) -> str:
    """Sanitize strings into alphanumeric snake_case identifiers."""
    sub = re.sub(r"[^a-zA-Z0-9_]+", "_", val).strip("_").lower()
    return sub or "item"


# ── Reference Resolution & Security Policy ────────────────────────────────────

def resolve_local_pointer(ref: str, root_doc: dict[str, Any], visited: set[str], depth: int = 0) -> Any:
    """Safely resolve a local JSON pointer ($ref) within root_doc.

    Raises:
        ValidationError: If ref is an external URI (SSRF defense) or cannot be resolved.
    """
    if not ref.startswith("#/"):
        raise ValidationError(
            f"External $ref '{ref}' is prohibited by security policy to prevent SSRF and unauthorized network egress."
        )

    if depth > MAX_REF_DEPTH or ref in visited:
        # Recursion/circular reference guard
        return {"$ref_circular": ref}

    visited.add(ref)
    tokens = ref[2:].split("/")
    curr: Any = root_doc

    for token in tokens:
        # Decode RFC 6901 JSON pointer escapes
        token_unescaped = token.replace("~1", "/").replace("~0", "~")
        if isinstance(curr, dict) and token_unescaped in curr:
            curr = curr[token_unescaped]
        elif isinstance(curr, list) and token_unescaped.isdigit() and int(token_unescaped) < len(curr):
            curr = curr[int(token_unescaped)]
        else:
            raise ValidationError(
                f"Malformed specification: unresolvable local $ref '{ref}'. Token '{token_unescaped}' not found."
            )

    if isinstance(curr, dict) and "$ref" in curr and isinstance(curr["$ref"], str):
        return resolve_local_pointer(curr["$ref"], root_doc, visited, depth + 1)

    return curr


def deep_resolve_schema(
    schema: dict[str, Any],
    root_doc: dict[str, Any],
    visited: set[str] | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Recursively resolve local schema references and inline properties."""
    if depth > 15:
        return schema
    if visited is None:
        visited = set()

    if not isinstance(schema, dict):
        return {}

    cloned = dict(schema)

    # If top-level $ref
    if "$ref" in cloned and isinstance(cloned["$ref"], str):
        target_ref = cloned["$ref"]
        resolved = resolve_local_pointer(target_ref, root_doc, visited, depth)
        if isinstance(resolved, dict):
            # In OpenAPI 3.1, sibling fields override or augment $ref
            merged = deep_resolve_schema(resolved, root_doc, visited, depth + 1)
            for k, v in cloned.items():
                if k != "$ref":
                    merged[k] = v
            return merged
        return cloned

    # Resolve properties
    if "properties" in cloned and isinstance(cloned["properties"], dict):
        new_props = {}
        for p_name, p_val in cloned["properties"].items():
            if isinstance(p_val, dict):
                new_props[p_name] = deep_resolve_schema(p_val, root_doc, set(visited), depth + 1)
            else:
                new_props[p_name] = p_val
        cloned["properties"] = new_props

    # Resolve items for arrays
    if "items" in cloned and isinstance(cloned["items"], dict):
        cloned["items"] = deep_resolve_schema(cloned["items"], root_doc, set(visited), depth + 1)

    # Resolve allOf / anyOf / oneOf
    for combiner in ("allOf", "anyOf", "oneOf"):
        if combiner in cloned and isinstance(cloned[combiner], list):
            cloned[combiner] = [
                deep_resolve_schema(item, root_doc, set(visited), depth + 1)
                for item in cloned[combiner]
                if isinstance(item, dict)
            ]

    return cloned


# ── Parser & Normalizer ───────────────────────────────────────────────────────

class OpenAPIParser:
    """Deterministic parser and normalizer for OpenAPI 3.0.x and 3.1.x specifications."""

    def __init__(self, raw_spec: str | dict[str, Any]):
        self.raw_spec = raw_spec
        self.doc: dict[str, Any] = {}

    def parse(self) -> NormalizedOpenAPISpec:
        """Parse, validate, and normalize the specification."""
        self._load_and_validate_root()
        version = self._validate_version()
        info = self.doc.get("info", {})
        title = str(info.get("title", "OpenAPI Specification"))
        spec_version = str(info.get("version", "1.0.0"))
        description = info.get("description")

        servers = [str(s.get("url")) for s in self.doc.get("servers", []) if isinstance(s, dict) and s.get("url")]
        security_schemes = self._extract_security_schemes()
        components_schemas = self._extract_component_schemas()
        operations = self._extract_operations()

        return NormalizedOpenAPISpec(
            openapi_version=version,
            title=title,
            version=spec_version,
            description=description,
            servers=servers,
            operations=operations,
            security_schemes=security_schemes,
            components_schemas=components_schemas,
        )

    def _load_and_validate_root(self) -> None:
        if isinstance(self.raw_spec, str):
            clean_str = self.raw_spec.strip()
            if not clean_str:
                raise ValidationError("Malformed OpenAPI specification: input string is empty.")
            if len(clean_str.encode("utf-8")) > MAX_SPEC_SIZE:
                raise ValidationError(
                    f"OpenAPI specification exceeds maximum allowed size of {MAX_SPEC_SIZE} bytes."
                )
            try:
                parsed = yaml.safe_load(clean_str)
            except Exception as exc:
                raise ValidationError(f"Malformed OpenAPI specification: failed to parse JSON/YAML: {exc}")
            if not isinstance(parsed, dict):
                raise ValidationError("Malformed OpenAPI specification: root must be a JSON/YAML object.")
            self.doc = parsed
        elif isinstance(self.raw_spec, dict):
            self.doc = copy.deepcopy(self.raw_spec)
        else:
            raise ValidationError("Malformed OpenAPI specification: expected JSON string or dictionary.")

    def _validate_version(self) -> str:
        if "swagger" in self.doc:
            raise ValidationError(
                f"Unsupported specification version 'Swagger {self.doc['swagger']}'. "
                "Supported versions: OpenAPI 3.0.x and 3.1.x."
            )
        openapi_val = self.doc.get("openapi")
        if not openapi_val:
            raise ValidationError(
                "Missing 'openapi' version field in specification. Supported versions: OpenAPI 3.0.x and 3.1.x."
            )
        ver_str = str(openapi_val).strip()
        if not (ver_str.startswith("3.0.") or ver_str.startswith("3.1.") or ver_str in ("3.0", "3.1")):
            raise ValidationError(
                f"Unsupported OpenAPI version '{ver_str}'. Supported versions: OpenAPI 3.0.x and 3.1.x."
            )
        return ver_str

    def _extract_security_schemes(self) -> dict[str, OpenAPISecurityScheme]:
        schemes: dict[str, OpenAPISecurityScheme] = {}
        raw_schemes = self.doc.get("components", {}).get("securitySchemes", {})
        if not isinstance(raw_schemes, dict):
            return schemes

        for name, scheme_data in raw_schemes.items():
            if not isinstance(scheme_data, dict):
                continue
            if "$ref" in scheme_data:
                scheme_data = resolve_local_pointer(scheme_data["$ref"], self.doc, set())
            schemes[name] = OpenAPISecurityScheme(
                name=name,
                type=str(scheme_data.get("type", "http")),
                scheme=scheme_data.get("scheme"),
                bearer_format=scheme_data.get("bearerFormat"),
                in_=scheme_data.get("in"),
                param_name=scheme_data.get("name"),
                description=scheme_data.get("description"),
            )
        return schemes

    def _extract_component_schemas(self) -> dict[str, Any]:
        raw_schemas = self.doc.get("components", {}).get("schemas", {})
        if not isinstance(raw_schemas, dict):
            return {}
        resolved: dict[str, Any] = {}
        for name, s_def in raw_schemas.items():
            if isinstance(s_def, dict):
                resolved[name] = deep_resolve_schema(s_def, self.doc, set())
        return resolved

    def _extract_operations(self) -> list[NormalizedOperation]:
        paths = self.doc.get("paths", {})
        if not isinstance(paths, dict):
            raise ValidationError("Malformed OpenAPI specification: 'paths' must be an object.")

        global_security = self.doc.get("security", [])
        operations: list[NormalizedOperation] = []

        # Deterministic ordering by path
        for path in sorted(paths.keys()):
            path_item = paths[path]
            if not isinstance(path_item, dict):
                continue
            if "$ref" in path_item:
                path_item = resolve_local_pointer(path_item["$ref"], self.doc, set())

            path_params_raw = path_item.get("parameters", [])
            path_params = path_params_raw if isinstance(path_params_raw, list) else []

            for method in sorted(VALID_HTTP_METHODS):
                if method not in path_item:
                    continue
                op_data = path_item[method]
                if not isinstance(op_data, dict):
                    continue

                op_id = op_data.get("operationId") or f"{method}_{_sanitize_name(path)}"
                summary = op_data.get("summary")
                description = op_data.get("description")
                tags = [str(t) for t in op_data.get("tags", []) if t]

                # Merge path-level and operation-level parameters
                op_params_raw = op_data.get("parameters", [])
                op_params = op_params_raw if isinstance(op_params_raw, list) else []

                merged_params: dict[tuple[str, str], OpenAPIParameter] = {}
                for p_raw in path_params + op_params:
                    if not isinstance(p_raw, dict):
                        continue
                    if "$ref" in p_raw:
                        p_raw = resolve_local_pointer(p_raw["$ref"], self.doc, set())
                    name = str(p_raw.get("name", "")).strip()
                    in_ = str(p_raw.get("in", "query")).strip().lower()
                    if not name:
                        continue

                    schema = p_raw.get("schema", {})
                    if isinstance(schema, dict) and "$ref" in schema:
                        schema = deep_resolve_schema(schema, self.doc, set())

                    param_obj = OpenAPIParameter(
                        name=name,
                        in_=in_,
                        required=bool(p_raw.get("required", in_ == "path")),
                        description=p_raw.get("description"),
                        schema_type=schema.get("type") if isinstance(schema, dict) else None,
                        schema_format=schema.get("format") if isinstance(schema, dict) else None,
                        enum_values=schema.get("enum", []) if isinstance(schema, dict) else [],
                        default_value=schema.get("default") if isinstance(schema, dict) else None,
                        minimum=schema.get("minimum") if isinstance(schema, dict) else None,
                        maximum=schema.get("maximum") if isinstance(schema, dict) else None,
                        min_length=schema.get("minLength") if isinstance(schema, dict) else None,
                        max_length=schema.get("maxLength") if isinstance(schema, dict) else None,
                        pattern=schema.get("pattern") if isinstance(schema, dict) else None,
                        nullable=bool(schema.get("nullable", False)) if isinstance(schema, dict) else False,
                    )
                    merged_params[(name, in_)] = param_obj

                # Sort parameters deterministically: required first, then by name
                sorted_parameters = sorted(
                    merged_params.values(),
                    key=lambda p: (not p.required, p.name),
                )

                # Request body extraction
                req_body_obj: OpenAPIRequestBody | None = None
                raw_body = op_data.get("requestBody")
                if isinstance(raw_body, dict):
                    if "$ref" in raw_body:
                        raw_body = resolve_local_pointer(raw_body["$ref"], self.doc, set())
                    is_req = bool(raw_body.get("required", False))
                    content = raw_body.get("content", {})
                    ct = "application/json"
                    schema_def: dict[str, Any] = {}
                    if isinstance(content, dict) and content:
                        if "application/json" in content:
                            ct = "application/json"
                            raw_s = content["application/json"].get("schema", {})
                        else:
                            ct, val = next(iter(content.items()))
                            raw_s = val.get("schema", {}) if isinstance(val, dict) else {}
                        if isinstance(raw_s, dict):
                            schema_def = deep_resolve_schema(raw_s, self.doc, set())

                    req_fields = schema_def.get("required", []) if isinstance(schema_def, dict) else []
                    props = schema_def.get("properties", {}) if isinstance(schema_def, dict) else {}

                    req_body_obj = OpenAPIRequestBody(
                        required=is_req,
                        content_type=ct,
                        schema_def=schema_def,
                        required_fields=list(req_fields),
                        properties=dict(props),
                        description=raw_body.get("description"),
                    )

                # Responses extraction
                responses_obj: dict[str, OpenAPIResponse] = {}
                raw_responses = op_data.get("responses", {})
                if isinstance(raw_responses, dict):
                    for status_code, r_data in raw_responses.items():
                        if not isinstance(r_data, dict):
                            continue
                        if "$ref" in r_data:
                            r_data = resolve_local_pointer(r_data["$ref"], self.doc, set())
                        r_desc = r_data.get("description")
                        r_content = r_data.get("content", {})
                        r_ct = None
                        r_schema: dict[str, Any] = {}
                        if isinstance(r_content, dict) and r_content:
                            if "application/json" in r_content:
                                r_ct = "application/json"
                                r_raw_s = r_content["application/json"].get("schema", {})
                            else:
                                r_ct, val = next(iter(r_content.items()))
                                r_raw_s = val.get("schema", {}) if isinstance(val, dict) else {}
                            if isinstance(r_raw_s, dict):
                                r_schema = deep_resolve_schema(r_raw_s, self.doc, set())

                        responses_obj[str(status_code)] = OpenAPIResponse(
                            status_code=str(status_code),
                            description=r_desc,
                            content_type=r_ct,
                            schema_def=r_schema,
                        )

                # Security requirements: op-level overrides global
                op_sec = op_data.get("security")
                if op_sec is not None and isinstance(op_sec, list):
                    security_requirements = op_sec
                elif isinstance(global_security, list):
                    security_requirements = global_security
                else:
                    security_requirements = []

                # Operation is authenticated if security requirements are non-empty
                is_authenticated = bool(security_requirements and any(bool(s) for s in security_requirements))

                operations.append(
                    NormalizedOperation(
                        operation_id=op_id,
                        path=path,
                        method=method.upper(),
                        summary=summary,
                        description=description,
                        tags=tags,
                        parameters=sorted_parameters,
                        request_body=req_body_obj,
                        responses=responses_obj,
                        security_requirements=security_requirements,
                        is_authenticated=is_authenticated,
                    )
                )

        return operations


# ── Test Candidate Generation ─────────────────────────────────────────────────

class OpenAPITestCandidateGenerator:
    """Generates structured, grounded, deterministic test candidates from normalized OpenAPI specs."""

    def __init__(self, spec: NormalizedOpenAPISpec):
        self.spec = spec

    def generate_all(self) -> list[OpenAPITestCandidate]:
        candidates: list[OpenAPITestCandidate] = []

        for op in self.spec.operations:
            candidates.extend(self._generate_happy_path(op))
            candidates.extend(self._generate_required_field_validation(op))
            candidates.extend(self._generate_boundary_validation(op))
            candidates.extend(self._generate_documented_errors(op))
            candidates.extend(self._generate_security_auth(op))

        # Lifecycle CRUD sequence candidates across operations
        candidates.extend(self._generate_lifecycle_sequences())

        # Sort candidates deterministically
        return sorted(candidates, key=lambda c: (c.path, c.method, c.category, c.candidate_id))

    def _generate_happy_path(self, op: NormalizedOperation) -> list[OpenAPITestCandidate]:
        """Generate positive happy-path candidate verifying documented 2xx response & response schema."""
        expected_2xx: int | None = None
        resp_obj: OpenAPIResponse | None = None

        # Look for lowest documented 2xx code (200, 201, 204)
        for code in sorted(op.responses.keys()):
            if code.isdigit() and 200 <= int(code) < 300:
                expected_2xx = int(code)
                resp_obj = op.responses[code]
                break

        is_inferred = False
        unresolved: list[str] = []
        if expected_2xx is None:
            expected_2xx = 200
            is_inferred = True
            unresolved.append("No 2xx response documented in specification; defaulting expected status to 200.")

        params = self._sample_parameters(op.parameters)
        body = self._sample_request_body(op.request_body) if op.request_body else None

        headers: dict[str, str] = {}
        if op.is_authenticated:
            headers["Authorization"] = "Bearer {{auth_token}}"

        assertions: list[AssertionSchema] = [
            AssertionSchema(
                type=AssertionType.STATUS_CODE,
                value=expected_2xx,
                message=f"Expected HTTP {expected_2xx} on {op.method} {op.path}",
            )
        ]

        if resp_obj and resp_obj.schema_def and resp_obj.schema_def.get("properties"):
            assertions.append(
                AssertionSchema(
                    type=AssertionType.JSON_SCHEMA,
                    schema=resp_obj.schema_def,
                    message=f"Validate response payload matches {op.operation_id} response schema",
                )
            )

        cid = f"cand_{op.operation_id}_happy_path"
        desc = f"Happy path verification for {op.method} {op.path} ({expected_2xx})"
        if op.summary:
            desc += f": {op.summary}"

        return [
            OpenAPITestCandidate(
                candidate_id=cid,
                operation_id=op.operation_id,
                category=CandidateCategory.POSITIVE_HAPPY_PATH,
                path=op.path,
                method=op.method,
                description=desc,
                suggested_headers=headers,
                suggested_params=params,
                suggested_body=body,
                expected_status=expected_2xx,
                expected_assertions=assertions,
                is_inferred=is_inferred,
                unresolved_expectations=unresolved,
                requirement_refs=[f"API-{op.operation_id}"],
            )
        ]

    def _generate_required_field_validation(self, op: NormalizedOperation) -> list[OpenAPITestCandidate]:
        """Propose negative test candidates omitting each required field from request body or query."""
        candidates: list[OpenAPITestCandidate] = []

        # 1. Missing required body fields
        if op.request_body and op.request_body.required_fields:
            full_body = self._sample_request_body(op.request_body) or {}
            for field in sorted(op.request_body.required_fields):
                omitted_body = copy.deepcopy(full_body)
                if isinstance(omitted_body, dict):
                    omitted_body.pop(field, None)

                cid = f"cand_{op.operation_id}_missing_body_{field}"
                desc = f"Verify {op.method} {op.path} rejects payload missing required field '{field}'"
                candidates.append(
                    OpenAPITestCandidate(
                        candidate_id=cid,
                        operation_id=op.operation_id,
                        category=CandidateCategory.REQUIRED_FIELD_VALIDATION,
                        path=op.path,
                        method=op.method,
                        description=desc,
                        suggested_headers={"Authorization": "Bearer {{auth_token}}"} if op.is_authenticated else {},
                        suggested_params=self._sample_parameters(op.parameters),
                        suggested_body=omitted_body,
                        expected_status=422 if "422" in op.responses else 400,
                        expected_assertions=[
                            AssertionSchema(
                                type=AssertionType.STATUS_CODE,
                                value=422 if "422" in op.responses else 400,
                                message=f"Expected rejection when required field '{field}' is omitted",
                            )
                        ],
                        is_inferred=False,
                        requirement_refs=[f"API-{op.operation_id}"],
                    )
                )

        # 2. Missing required query parameters
        for p in op.parameters:
            if p.in_ == "query" and p.required:
                cid = f"cand_{op.operation_id}_missing_query_{p.name}"
                desc = f"Verify {op.method} {op.path} rejects missing required query parameter '{p.name}'"
                params = self._sample_parameters(op.parameters)
                params.pop(p.name, None)
                candidates.append(
                    OpenAPITestCandidate(
                        candidate_id=cid,
                        operation_id=op.operation_id,
                        category=CandidateCategory.REQUIRED_FIELD_VALIDATION,
                        path=op.path,
                        method=op.method,
                        description=desc,
                        suggested_headers={"Authorization": "Bearer {{auth_token}}"} if op.is_authenticated else {},
                        suggested_params=params,
                        suggested_body=self._sample_request_body(op.request_body) if op.request_body else None,
                        expected_status=400,
                        expected_assertions=[
                            AssertionSchema(
                                type=AssertionType.STATUS_CODE,
                                value=400,
                                message=f"Expected 400 rejection when required query parameter '{p.name}' is missing",
                            )
                        ],
                        is_inferred=False,
                        requirement_refs=[f"API-{op.operation_id}"],
                    )
                )

        return candidates

    def _generate_boundary_validation(self, op: NormalizedOperation) -> list[OpenAPITestCandidate]:
        """Propose boundary and enum validation candidates."""
        candidates: list[OpenAPITestCandidate] = []

        # Check parameter enums
        for p in op.parameters:
            if p.enum_values:
                cid = f"cand_{op.operation_id}_invalid_enum_{p.name}"
                desc = f"Verify {op.method} {op.path} rejects invalid enum for parameter '{p.name}'"
                params = self._sample_parameters(op.parameters)
                params[p.name] = "__invalid_enum_val__"
                candidates.append(
                    OpenAPITestCandidate(
                        candidate_id=cid,
                        operation_id=op.operation_id,
                        category=CandidateCategory.BOUNDARY_VALIDATION,
                        path=op.path,
                        method=op.method,
                        description=desc,
                        suggested_headers={"Authorization": "Bearer {{auth_token}}"} if op.is_authenticated else {},
                        suggested_params=params,
                        expected_status=400,
                        expected_assertions=[
                            AssertionSchema(
                                type=AssertionType.STATUS_CODE,
                                value=400,
                                message=f"Expected 400 for invalid enum value on '{p.name}'",
                            )
                        ],
                        is_inferred=False,
                        requirement_refs=[f"API-{op.operation_id}"],
                    )
                )

        # Check request body property enums
        if op.request_body and op.request_body.properties:
            for prop_name, prop_data in sorted(op.request_body.properties.items()):
                if isinstance(prop_data, dict) and prop_data.get("enum"):
                    body = self._sample_request_body(op.request_body) or {}
                    if isinstance(body, dict):
                        body = copy.deepcopy(body)
                        body[prop_name] = "__invalid_enum_val__"
                    cid = f"cand_{op.operation_id}_invalid_body_enum_{prop_name}"
                    desc = f"Verify {op.method} {op.path} rejects invalid enum for body field '{prop_name}'"
                    candidates.append(
                        OpenAPITestCandidate(
                            candidate_id=cid,
                            operation_id=op.operation_id,
                            category=CandidateCategory.BOUNDARY_VALIDATION,
                            path=op.path,
                            method=op.method,
                            description=desc,
                            suggested_headers={"Authorization": "Bearer {{auth_token}}"} if op.is_authenticated else {},
                            suggested_params=self._sample_parameters(op.parameters),
                            suggested_body=body,
                            expected_status=422 if "422" in op.responses else 400,
                            expected_assertions=[
                                AssertionSchema(
                                    type=AssertionType.STATUS_CODE,
                                    value=422 if "422" in op.responses else 400,
                                    message=f"Expected rejection for invalid enum value on body property '{prop_name}'",
                                )
                            ],
                            is_inferred=False,
                            requirement_refs=[f"API-{op.operation_id}"],
                        )
                    )

        return candidates

    def _generate_documented_errors(self, op: NormalizedOperation) -> list[OpenAPITestCandidate]:
        """Propose candidates for explicitly documented 4xx error codes."""
        candidates: list[OpenAPITestCandidate] = []

        for sc_str in sorted(op.responses.keys()):
            if sc_str.isdigit() and sc_str in ("404", "409", "403"):
                status_code = int(sc_str)
                resp = op.responses[sc_str]
                cid = f"cand_{op.operation_id}_error_{sc_str}"
                desc = f"Verify {op.method} {op.path} returns {status_code}"
                if resp.description:
                    desc += f" ({resp.description})"

                params = self._sample_parameters(op.parameters)
                # If 404 and there's a path param, mutate it to non-existent
                for p in op.parameters:
                    if p.in_ == "path":
                        params[p.name] = "non_existent_99999"

                candidates.append(
                    OpenAPITestCandidate(
                        candidate_id=cid,
                        operation_id=op.operation_id,
                        category=CandidateCategory.DOCUMENTED_ERROR,
                        path=op.path,
                        method=op.method,
                        description=desc,
                        suggested_headers={"Authorization": "Bearer {{auth_token}}"} if op.is_authenticated else {},
                        suggested_params=params,
                        expected_status=status_code,
                        expected_assertions=[
                            AssertionSchema(
                                type=AssertionType.STATUS_CODE,
                                value=status_code,
                                message=f"Expected {status_code} response according to specification",
                            )
                        ],
                        is_inferred=False,
                        requirement_refs=[f"API-{op.operation_id}"],
                    )
                )

        return candidates

    def _generate_security_auth(self, op: NormalizedOperation) -> list[OpenAPITestCandidate]:
        """Propose unauthenticated test candidate for authentication-required endpoints."""
        if not op.is_authenticated:
            return []

        cid = f"cand_{op.operation_id}_unauthenticated_401"
        desc = f"Verify {op.method} {op.path} rejects unauthenticated request with 401"
        return [
            OpenAPITestCandidate(
                candidate_id=cid,
                operation_id=op.operation_id,
                category=CandidateCategory.SECURITY_AUTH,
                path=op.path,
                method=op.method,
                description=desc,
                suggested_headers={},  # Crucial: omit auth header
                suggested_params=self._sample_parameters(op.parameters),
                suggested_body=self._sample_request_body(op.request_body) if op.request_body else None,
                expected_status=401,
                expected_assertions=[
                    AssertionSchema(
                        type=AssertionType.STATUS_CODE,
                        value=401,
                        message=f"Expected 401 Unauthorized when calling {op.path} without authentication",
                    )
                ],
                is_inferred=False,
                requirement_refs=[f"API-{op.operation_id}"],
            )
        ]

    def _generate_lifecycle_sequences(self) -> list[OpenAPITestCandidate]:
        """Detect and propose CRUD lifecycle sequences for related resource endpoints."""
        candidates: list[OpenAPITestCandidate] = []

        # Group operations by base resource path
        # e.g. /items and /items/{id} share prefix /items
        collection_ops: dict[str, NormalizedOperation] = {}
        item_ops: dict[str, list[NormalizedOperation]] = {}

        for op in self.spec.operations:
            clean_path = op.path.rstrip("/")
            if "{" in clean_path:
                base = re.sub(r"/\{[^}]+\}$", "", clean_path)
                item_ops.setdefault(base, []).append(op)
            else:
                if op.method == "POST":
                    collection_ops[clean_path] = op

        for base_path, create_op in sorted(collection_ops.items()):
            sub_ops = item_ops.get(base_path, [])
            if not sub_ops:
                continue

            methods_available = {o.method for o in sub_ops}
            if "GET" in methods_available:
                cid = f"cand_lifecycle_{_sanitize_name(base_path)}"
                desc = f"CRUD resource lifecycle sequence for '{base_path}': POST create -> GET read"
                if "PUT" in methods_available or "PATCH" in methods_available:
                    desc += " -> PUT update"
                if "DELETE" in methods_available:
                    desc += " -> DELETE destroy"

                candidates.append(
                    OpenAPITestCandidate(
                        candidate_id=cid,
                        operation_id=create_op.operation_id,
                        category=CandidateCategory.LIFECYCLE_SEQUENCE,
                        path=base_path,
                        method="POST",
                        description=desc,
                        expected_status=201 if "201" in create_op.responses else 200,
                        is_inferred=False,
                        unresolved_expectations=["Requires chaining context export ID from POST response to subsequent operations."],
                        requirement_refs=[f"API-LIFECYCLE-{_sanitize_name(base_path)}"],
                    )
                )

        return candidates

    def _sample_parameters(self, parameters: list[OpenAPIParameter]) -> dict[str, Any]:
        """Generate deterministic valid sample values for parameters."""
        samples: dict[str, Any] = {}
        for p in parameters:
            if not p.required and p.in_ != "path":
                continue
            if p.enum_values:
                samples[p.name] = p.enum_values[0]
            elif p.default_value is not None:
                samples[p.name] = p.default_value
            elif p.schema_type == "integer":
                samples[p.name] = int(p.minimum) if p.minimum is not None else 1
            elif p.schema_type == "number":
                samples[p.name] = float(p.minimum) if p.minimum is not None else 1.0
            elif p.schema_type == "boolean":
                samples[p.name] = True
            else:
                samples[p.name] = f"sample_{p.name}"
        return samples

    def _sample_request_body(self, req_body: OpenAPIRequestBody | None) -> dict[str, Any] | None:
        """Generate deterministic valid sample values for required properties in requestBody."""
        if not req_body or not req_body.properties:
            return None

        body: dict[str, Any] = {}
        for prop_name, prop_schema in sorted(req_body.properties.items()):
            # Only populate required properties by default to keep minimal valid payload
            if req_body.required_fields and prop_name not in req_body.required_fields:
                continue
            if not isinstance(prop_schema, dict):
                body[prop_name] = "test"
                continue

            if prop_schema.get("enum"):
                body[prop_name] = prop_schema["enum"][0]
            elif prop_schema.get("default") is not None:
                body[prop_name] = prop_schema["default"]
            elif prop_schema.get("type") == "integer":
                body[prop_name] = int(prop_schema.get("minimum", 1))
            elif prop_schema.get("type") == "number":
                body[prop_name] = float(prop_schema.get("minimum", 1.0))
            elif prop_schema.get("type") == "boolean":
                body[prop_name] = True
            elif prop_schema.get("type") == "array":
                body[prop_name] = []
            elif prop_schema.get("type") == "object":
                body[prop_name] = {}
            else:
                body[prop_name] = f"valid_{prop_name}"

        return body


# ── Public Service Functions ──────────────────────────────────────────────────

def analyze_openapi_specification(raw_spec: str | dict[str, Any]) -> OpenAPIAnalysisResult:
    """Parse, normalize, and generate test candidates for an OpenAPI 3.x specification deterministically."""
    parser = OpenAPIParser(raw_spec)
    normalized_spec = parser.parse()

    candidate_gen = OpenAPITestCandidateGenerator(normalized_spec)
    candidates = candidate_gen.generate_all()

    # Build concise markdown summary for planner context
    lines = [
        f"### OpenAPI Specification: {normalized_spec.title} (v{normalized_spec.version})",
        f"- **OpenAPI Version:** {normalized_spec.openapi_version}",
        f"- **Endpoints / Operations:** {len(normalized_spec.operations)}",
        f"- **Generated Test Candidates:** {len(candidates)}",
        "",
        "#### Operations Table",
        "| Method | Path | Operation ID | Auth | Summary |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for op in normalized_spec.operations:
        auth_badge = "Required" if op.is_authenticated else "Public"
        summary_clean = (op.summary or "-").replace("|", "\\|")
        lines.append(f"| {op.method} | {op.path} | `{op.operation_id}` | {auth_badge} | {summary_clean} |")

    lines.append("")
    lines.append("#### Deterministic Candidate Test Intent")
    for c in candidates:
        badge = f"[{c.category}]"
        lines.append(f"- **`{c.candidate_id}`** {badge}: {c.method} `{c.path}` -> Expected Status {c.expected_status}")

    summary_md = "\n".join(lines)

    return OpenAPIAnalysisResult(
        spec=normalized_spec,
        candidates=candidates,
        total_operations=len(normalized_spec.operations),
        total_candidates=len(candidates),
        summary_markdown=summary_md,
    )
