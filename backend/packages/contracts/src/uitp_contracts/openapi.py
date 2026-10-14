"""
uitp_contracts.openapi
======================
Contracts and structured data models for deterministic OpenAPI specification
analysis, endpoint normalization, and test candidate generation (Task 6).
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.plan import AssertionSchema


class CandidateCategory(StrEnum):
    """Category classification of generated API test candidates."""

    POSITIVE_HAPPY_PATH = "positive_happy_path"
    REQUIRED_FIELD_VALIDATION = "required_field_validation"
    BOUNDARY_VALIDATION = "boundary_validation"
    DOCUMENTED_ERROR = "documented_error"
    SECURITY_AUTH = "security_auth"
    SCHEMA_VALIDATION = "schema_validation"
    LIFECYCLE_SEQUENCE = "lifecycle_sequence"


class OpenAPISecurityScheme(BaseModel):
    """Normalized security scheme definition from components.securitySchemes."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: str  # http, apiKey, oauth2, openIdConnect
    scheme: str | None = None  # bearer, basic
    bearer_format: str | None = None  # JWT, etc.
    in_: str | None = None  # header, query, cookie
    param_name: str | None = None  # Header/param name if apiKey
    description: str | None = None


class OpenAPIParameter(BaseModel):
    """Normalized operation parameter."""

    model_config = ConfigDict(frozen=True)

    name: str
    in_: str  # path, query, header, cookie
    required: bool = False
    description: str | None = None
    schema_type: str | None = None
    schema_format: str | None = None
    enum_values: list[Any] = Field(default_factory=list)
    default_value: Any = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    nullable: bool = False


class OpenAPIRequestBody(BaseModel):
    """Normalized request body for an operation."""

    model_config = ConfigDict(frozen=True)

    required: bool = False
    content_type: str = "application/json"
    schema_def: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class OpenAPIResponse(BaseModel):
    """Normalized response definition for a given HTTP status code."""

    model_config = ConfigDict(frozen=True)

    status_code: str
    description: str | None = None
    content_type: str | None = None
    schema_def: dict[str, Any] = Field(default_factory=dict)


class NormalizedOperation(BaseModel):
    """Fully normalized and resolved OpenAPI operation."""

    model_config = ConfigDict(frozen=True)

    operation_id: str
    path: str
    method: str  # GET, POST, PUT, DELETE, PATCH, etc. (UPPERCASE)
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: list[OpenAPIParameter] = Field(default_factory=list)
    request_body: OpenAPIRequestBody | None = None
    responses: dict[str, OpenAPIResponse] = Field(default_factory=dict)
    security_requirements: list[dict[str, list[str]]] = Field(default_factory=list)
    is_authenticated: bool = False


class NormalizedOpenAPISpec(BaseModel):
    """Structured normalized OpenAPI specification representation."""

    model_config = ConfigDict(frozen=True)

    openapi_version: str
    title: str
    version: str
    description: str | None = None
    servers: list[str] = Field(default_factory=list)
    operations: list[NormalizedOperation] = Field(default_factory=list)
    security_schemes: dict[str, OpenAPISecurityScheme] = Field(default_factory=dict)
    components_schemas: dict[str, Any] = Field(default_factory=dict)


class OpenAPITestCandidate(BaseModel):
    """Deterministic test candidate proposed from the normalized specification."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    operation_id: str
    category: CandidateCategory
    path: str
    method: str
    description: str
    suggested_headers: dict[str, str] = Field(default_factory=dict)
    suggested_params: dict[str, Any] = Field(default_factory=dict)
    suggested_body: Any = None
    expected_status: int = 200
    expected_assertions: list[AssertionSchema] = Field(default_factory=list)
    is_inferred: bool = False
    unresolved_expectations: list[str] = Field(default_factory=list)
    requirement_refs: list[str] = Field(default_factory=list)


class OpenAPIAnalysisResult(BaseModel):
    """Result of deterministic OpenAPI parsing, normalization, and candidate generation."""

    model_config = ConfigDict(frozen=True)

    spec: NormalizedOpenAPISpec
    candidates: list[OpenAPITestCandidate] = Field(default_factory=list)
    total_operations: int
    total_candidates: int
    summary_markdown: str


class AnalyzeOpenAPIRequest(BaseModel):
    """Request payload for deterministic OpenAPI analysis endpoint."""

    model_config = ConfigDict(frozen=True)

    openapi_spec: str = Field(..., min_length=1, max_length=262144, description="OpenAPI specification in JSON or YAML")
