"""
tests/unit/test_openapi_analysis.py
===================================
Unit tests for deterministic OpenAPI 3.0 / 3.1 parsing, normalization,
security policy enforcement, and test candidate generation (Task 6).
"""
import pytest
import yaml

from uitp_api.exceptions import ValidationError
from uitp_api.services.openapi_service import (
    MAX_SPEC_SIZE,
    OpenAPIParser,
    analyze_openapi_specification,
)
from uitp_contracts.openapi import CandidateCategory


SAMPLE_OPENAPI_3_0_YAML = """
openapi: "3.0.3"
info:
  title: "Petstore Mini API"
  version: "1.0.0"
  description: "A sample deterministic API for testing."
servers:
  - url: "http://mock-api.local"
components:
  securitySchemes:
    BearerAuth:
      type: "http"
      scheme: "bearer"
      bearerFormat: "JWT"
    ApiKeyAuth:
      type: "apiKey"
      in: "header"
      name: "X-API-Key"
  schemas:
    Category:
      type: "object"
      required:
        - "name"
      properties:
        id:
          type: "integer"
        name:
          type: "string"
    Pet:
      type: "object"
      required:
        - "name"
        - "status"
      properties:
        id:
          type: "integer"
        name:
          type: "string"
        status:
          type: "string"
          enum: ["available", "pending", "sold"]
        category:
          $ref: "#/components/schemas/Category"
paths:
  /pets:
    get:
      operationId: "listPets"
      summary: "List all pets"
      parameters:
        - name: "limit"
          in: "query"
          required: false
          schema:
            type: "integer"
            default: 20
        - name: "status"
          in: "query"
          required: false
          schema:
            type: "string"
            enum: ["available", "pending", "sold"]
      responses:
        "200":
          description: "A list of pets"
          content:
            application/json:
              schema:
                type: "array"
                items:
                  $ref: "#/components/schemas/Pet"
    post:
      operationId: "createPet"
      summary: "Create a new pet"
      security:
        - BearerAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/Pet"
      responses:
        "201":
          description: "Pet created"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pet"
        "422":
          description: "Validation error"
  /pets/{petId}:
    parameters:
      - name: "petId"
        in: "path"
        required: true
        schema:
          type: "integer"
    get:
      operationId: "getPetById"
      summary: "Get pet by ID"
      responses:
        "200":
          description: "Pet found"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pet"
        "404":
          description: "Pet not found"
    delete:
      operationId: "deletePet"
      summary: "Delete a pet"
      security:
        - ApiKeyAuth: []
      responses:
        "204":
          description: "Pet deleted"
        "404":
          description: "Pet not found"
"""

SAMPLE_OPENAPI_3_1_JSON = """
{
  "openapi": "3.1.0",
  "info": {
    "title": "Modern Service API",
    "version": "2.0.0"
  },
  "paths": {
    "/items": {
      "post": {
        "operationId": "createItem",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["sku", "price"],
                "properties": {
                  "sku": {"type": "string", "minLength": 3},
                  "price": {"type": "number", "minimum": 0.01}
                }
              }
            }
          }
        },
        "responses": {
          "201": {
            "description": "Item created"
          }
        }
      }
    }
  }
}
"""


class TestOpenAPIParserAndNormalizer:
    def test_openapi_3_0_parsing_and_component_normalization(self):
        """Successfully parses OpenAPI 3.0.3, resolves local schema references, and normalizes components."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        spec = result.spec

        assert spec.openapi_version == "3.0.3"
        assert spec.title == "Petstore Mini API"
        assert spec.version == "1.0.0"
        assert len(spec.servers) == 1
        assert spec.servers[0] == "http://mock-api.local"

        # Check security schemes
        assert "BearerAuth" in spec.security_schemes
        assert spec.security_schemes["BearerAuth"].type == "http"
        assert spec.security_schemes["BearerAuth"].scheme == "bearer"
        assert "ApiKeyAuth" in spec.security_schemes
        assert spec.security_schemes["ApiKeyAuth"].in_ == "header"
        assert spec.security_schemes["ApiKeyAuth"].param_name == "X-API-Key"

        # Check operations
        ops_by_id = {op.operation_id: op for op in spec.operations}
        assert "listPets" in ops_by_id
        assert "createPet" in ops_by_id
        assert "getPetById" in ops_by_id
        assert "deletePet" in ops_by_id

        # Verify authentication resolution
        assert not ops_by_id["listPets"].is_authenticated
        assert ops_by_id["createPet"].is_authenticated
        assert not ops_by_id["getPetById"].is_authenticated
        assert ops_by_id["deletePet"].is_authenticated

        # Verify deep schema resolution for requestBody
        create_op = ops_by_id["createPet"]
        assert create_op.request_body is not None
        assert create_op.request_body.required is True
        assert set(create_op.request_body.required_fields) == {"name", "status"}
        assert "category" in create_op.request_body.properties
        # category was a $ref to Category, must be deeply resolved
        assert create_op.request_body.properties["category"].get("type") == "object"
        assert "name" in create_op.request_body.properties["category"].get("properties", {})

    def test_openapi_3_1_json_support(self):
        """Supports OpenAPI 3.1.0 specifications formatted as JSON."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_1_JSON)
        assert result.spec.openapi_version == "3.1.0"
        assert result.spec.title == "Modern Service API"
        assert len(result.spec.operations) == 1
        op = result.spec.operations[0]
        assert op.operation_id == "createItem"
        assert op.method == "POST"
        assert op.path == "/items"
        assert op.request_body is not None
        assert set(op.request_body.required_fields) == {"sku", "price"}

    def test_swagger_2_rejected(self):
        import textwrap

        swagger_spec = textwrap.dedent("""
            swagger: "2.0"
            info:
              title: "Old API"
              version: "1.0"
            paths: {}
        """)
        with pytest.raises(ValidationError) as exc_info:
            analyze_openapi_specification(swagger_spec)
        assert "Swagger 2.0" in str(exc_info.value)
        assert "Supported versions: OpenAPI 3.0.x and 3.1.x" in str(exc_info.value)

    def test_missing_or_unsupported_openapi_version_rejected(self):
        """Missing or unsupported OpenAPI versions are rejected."""
        import textwrap

        no_ver = textwrap.dedent("""
            info:
              title: "No Version"
              version: "1.0"
            paths: {}
        """)
        with pytest.raises(ValidationError) as exc:
            analyze_openapi_specification(no_ver)
        assert "Missing 'openapi' version" in str(exc.value)

        bad_ver = textwrap.dedent("""
            openapi: "2.5.0"
            info:
              title: "Unsupported"
              version: "1.0"
            paths: {}
        """)
        with pytest.raises(ValidationError) as exc2:
            analyze_openapi_specification(bad_ver)
        assert "Unsupported OpenAPI version '2.5.0'" in str(exc2.value)

    def test_external_ref_prohibited_by_security_policy(self):
        """External schema references ($ref) are strictly prohibited to prevent SSRF and exfiltration."""
        import textwrap

        ssrf_spec = textwrap.dedent("""
            openapi: "3.0.3"
            info:
              title: "SSRF Attack Spec"
              version: "1.0"
            paths:
              /data:
                post:
                  operationId: "postData"
                  requestBody:
                    content:
                      application/json:
                        schema:
                          $ref: "https://evil-attacker.local/malicious.json"
                  responses:
                    "200":
                      description: "OK"
        """)
        with pytest.raises(ValidationError) as exc:
            analyze_openapi_specification(ssrf_spec)
        assert "External $ref" in str(exc.value)
        assert "prohibited by security policy" in str(exc.value)

    def test_malformed_yaml_json_and_empty_spec(self):
        """Malformed YAML/JSON and empty inputs are rejected cleanly."""
        with pytest.raises(ValidationError) as exc1:
            analyze_openapi_specification("   ")
        assert "input string is empty" in str(exc1.value)

        with pytest.raises(ValidationError) as exc2:
            analyze_openapi_specification("paths: [unclosed list")
        assert "failed to parse JSON/YAML" in str(exc2.value)

        with pytest.raises(ValidationError) as exc3:
            analyze_openapi_specification("- a list instead of object")
        assert "root must be a JSON/YAML object" in str(exc3.value)

    def test_size_limit_rejection(self):
        """Specifications exceeding the 256 KB size limit are rejected immediately."""
        large_comment = "# " + ("x" * (MAX_SPEC_SIZE + 100))
        oversized_spec = f"""
        openapi: "3.0.3"
        info:
          title: "Huge"
          version: "1.0"
        {large_comment}
        paths: {{}}
        """
        with pytest.raises(ValidationError) as exc:
            analyze_openapi_specification(oversized_spec)
        assert "exceeds maximum allowed size" in str(exc.value)


class TestCandidateGeneration:
    def test_deterministic_candidate_generation(self):
        """Candidate generation produces complete, repeatable, grounded test candidates."""
        res1 = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        res2 = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)

        assert res1.total_candidates > 0
        assert res1.total_candidates == res2.total_candidates

        # Ensure candidate IDs and ordering are completely stable
        c_ids_1 = [c.candidate_id for c in res1.candidates]
        c_ids_2 = [c.candidate_id for c in res2.candidates]
        assert c_ids_1 == c_ids_2

        # Verify candidate categories
        categories = {c.category for c in res1.candidates}
        assert CandidateCategory.POSITIVE_HAPPY_PATH in categories
        assert CandidateCategory.REQUIRED_FIELD_VALIDATION in categories
        assert CandidateCategory.BOUNDARY_VALIDATION in categories
        assert CandidateCategory.DOCUMENTED_ERROR in categories
        assert CandidateCategory.SECURITY_AUTH in categories
        assert CandidateCategory.LIFECYCLE_SEQUENCE in categories

    def test_happy_path_assertions(self):
        """Happy path candidates carry expected 2xx status codes and JSON schema assertions."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        happy_candidates = [c for c in result.candidates if c.category == CandidateCategory.POSITIVE_HAPPY_PATH]

        # GET /pets -> 200
        get_pets = next(c for c in happy_candidates if c.operation_id == "listPets")
        assert get_pets.expected_status == 200
        assert any(a.type == "status_code" and a.value == 200 for a in get_pets.expected_assertions)

        # POST /pets -> 201
        create_pet = next(c for c in happy_candidates if c.operation_id == "createPet")
        assert create_pet.expected_status == 201
        assert any(a.type == "status_code" and a.value == 201 for a in create_pet.expected_assertions)
        # Check that response schema assertion is attached
        assert any(a.type == "json_schema" and a.schema_ is not None for a in create_pet.expected_assertions)

    def test_required_field_omission_candidates(self):
        """Required fields generate negative test candidates expecting rejection."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        req_candidates = [c for c in result.candidates if c.category == CandidateCategory.REQUIRED_FIELD_VALIDATION]

        # Pet requires 'name' and 'status'
        missing_name = next((c for c in req_candidates if "missing_body_name" in c.candidate_id), None)
        assert missing_name is not None
        assert missing_name.expected_status in (400, 422)
        assert isinstance(missing_name.suggested_body, dict)
        assert "name" not in missing_name.suggested_body
        assert "status" in missing_name.suggested_body

        missing_status = next((c for c in req_candidates if "missing_body_status" in c.candidate_id), None)
        assert missing_status is not None
        assert "status" not in missing_status.suggested_body

    def test_enum_boundary_validation_candidates(self):
        """Enums generate boundary validation test candidates."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        boundary_candidates = [c for c in result.candidates if c.category == CandidateCategory.BOUNDARY_VALIDATION]

        # Status query param has enum
        query_enum = next((c for c in boundary_candidates if "invalid_enum_status" in c.candidate_id), None)
        assert query_enum is not None
        assert query_enum.suggested_params.get("status") == "__invalid_enum_val__"

        # Pet body status has enum
        body_enum = next((c for c in boundary_candidates if "invalid_body_enum_status" in c.candidate_id), None)
        assert body_enum is not None
        assert body_enum.suggested_body.get("status") == "__invalid_enum_val__"

    def test_security_auth_candidates(self):
        """Authenticated endpoints generate unauthenticated candidates expecting 401."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        auth_candidates = [c for c in result.candidates if c.category == CandidateCategory.SECURITY_AUTH]

        # createPet requires BearerAuth -> must generate unauthenticated test
        unauth_create = next(c for c in auth_candidates if c.operation_id == "createPet")
        assert unauth_create.expected_status == 401
        assert "Authorization" not in unauth_create.suggested_headers

        # deletePet requires ApiKeyAuth -> must generate unauthenticated test
        unauth_delete = next(c for c in auth_candidates if c.operation_id == "deletePet")
        assert unauth_delete.expected_status == 401
        assert "X-API-Key" not in unauth_delete.suggested_headers

    def test_documented_error_candidates(self):
        """Documented 404 responses generate negative error test candidates."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        error_candidates = [c for c in result.candidates if c.category == CandidateCategory.DOCUMENTED_ERROR]

        get_pet_404 = next(c for c in error_candidates if c.operation_id == "getPetById" and c.expected_status == 404)
        assert get_pet_404.suggested_params.get("petId") == "non_existent_99999"

    def test_crud_lifecycle_sequence_detection(self):
        """Collection /pets (POST) and item /pets/{petId} (GET, DELETE) generate lifecycle sequence."""
        result = analyze_openapi_specification(SAMPLE_OPENAPI_3_0_YAML)
        lifecycle = [c for c in result.candidates if c.category == CandidateCategory.LIFECYCLE_SEQUENCE]
        assert len(lifecycle) >= 1
        pets_lifecycle = next(c for c in lifecycle if c.path == "/pets")
        assert "CRUD resource lifecycle sequence for '/pets'" in pets_lifecycle.description
        assert pets_lifecycle.is_inferred is False
        assert len(pets_lifecycle.unresolved_expectations) > 0
