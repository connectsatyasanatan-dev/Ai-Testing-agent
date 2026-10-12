"""
uitp_api.models
===============
SQLAlchemy ORM model registry.

Import all model classes here so that Alembic's env.py can discover them
via a single import of this module.
"""
from uitp_api.models.adapter import AdapterRegistration
from uitp_api.models.audit import AuditEvent
from uitp_api.models.environment import Environment
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.plan import TestPlan, TestStep
from uitp_api.models.project import Project, ProjectMembership
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.models.user import User

__all__ = [
    "User",
    "Project",
    "ProjectMembership",
    "Environment",
    "TestPlan",
    "TestStep",
    "Run",
    "StepAttempt",
    "RunEvent",
    "EvidenceArtifact",
    "AuditEvent",
    "AdapterRegistration",
]
