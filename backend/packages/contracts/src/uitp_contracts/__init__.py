"""
uitp_contracts
==============
Single source of truth for all shared data schemas.

Import from sub-modules:
    from uitp_contracts.enums import RunStatus, IdempotencyClass
    from uitp_contracts.plan import (
        TestPlanSchema,
        TestStepSchema,
        CreatePlanRequest,
        CreatePlanVersionRequest,
        ApprovePlanRequest,
        PlanVersionsResponse,
    )
    from uitp_contracts.run import RunSchema, StepAttemptSchema
    from uitp_contracts.environment import EnvironmentSchema
    from uitp_contracts.evidence import EvidenceArtifactSchema
    from uitp_contracts.adapter import AdapterCapabilities
    from uitp_contracts.errors import ErrorResponse
"""

