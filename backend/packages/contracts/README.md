# uitp-contracts

Shared Pydantic schemas for the Universal AI Testing Platform.

This package is the **single source of truth** for all shared data types:
plans, steps, runs, evidence metadata, and adapter capabilities.

TypeScript types are generated from the FastAPI OpenAPI schema (which
is built from these models) using `openapi-typescript`. See ADR-010
and `make generate-types`.

## Contents (added in P0-02)

Schemas are added in task P0-02. This package skeleton is created in P0-01.
