"""
uitp_api.services.environment_service
=====================================
Business logic for managing project environments.

Enforces:
  - Role-based authorization and cross-tenant isolation.
  - Soft-delete semantics (status='archived').
  - Safe audit logging without logging secret values or credentials.
  - Transaction commit ownership.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from uitp_api.models.environment import Environment
from uitp_api.repositories.environment_repo import EnvironmentRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import audit_service
from uitp_contracts.enums import AuditEventType

logger = logging.getLogger(__name__)

SUPPORTED_UPDATE_FIELDS = frozenset(
    {"name", "base_url", "allowlist", "secret_refs", "description", "status"}
)


async def _assert_project_access(
    session: AsyncSession,
    project_id: str,
    actor_id: str,
    allowed_roles: tuple[str, ...],
) -> None:
    """Validate project existence and actor role.

    Masks cross-tenant existence: returns 404 for non-members.
    Returns 403 for members lacking necessary role.
    """
    project_repo = ProjectRepository(session)
    project = await project_repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    membership = await project_repo.get_membership(project_id, actor_id)
    if membership is None:
        raise ResourceNotFoundError("Project not found.")

    if membership.role not in allowed_roles:
        raise PermissionDeniedError(
            f"Insufficient permissions: requires one of {', '.join(allowed_roles)}."
        )


async def create_environment(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    name: str,
    base_url: str,
    allowlist: list[str] | None = None,
    secret_refs: dict[str, str] | None = None,
    description: str | None = None,
    ip_address: str | None = None,
) -> Environment:
    """Create a new environment in the given project.

    Requires Owner or Maintainer role.
    """
    await _assert_project_access(
        session, project_id, actor_id, ("owner", "maintainer")
    )

    env = Environment(
        id=str(uuid.uuid4()),
        project_id=project_id,
        name=name,
        base_url=base_url,
        allowlist=allowlist,
        secret_refs=secret_refs,
        description=description,
        status="active",
    )
    session.add(env)

    # Safe audit metadata: only record secret reference keys, NEVER secret values.
    metadata: dict[str, Any] = {
        "name": name,
        "base_url": base_url,
    }
    if secret_refs:
        metadata["secret_ref_keys"] = sorted(list(secret_refs.keys()))

    await audit_service.record(
        session,
        event_type=AuditEventType.ENVIRONMENT_CREATED,
        actor_id=actor_id,
        project_id=project_id,
        resource_type="environment",
        resource_id=env.id,
        ip_address=ip_address,
        metadata=metadata,
    )

    await session.commit()
    await session.refresh(env)
    logger.info("Environment created: %s in project %s", env.id, project_id)
    return env


async def list_environments(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    include_archived: bool = False,
) -> list[Environment]:
    """List all active (or optionally all) environments for a project accessible to actor."""
    await _assert_project_access(
        session, project_id, actor_id, ("owner", "maintainer", "viewer")
    )
    repo = EnvironmentRepository(session)
    return await repo.list_for_project(project_id, include_archived=include_archived)


async def delete_environment(
    session: AsyncSession,
    *,
    project_id: str,
    environment_id: str,
    actor_id: str,
    ip_address: str | None = None,
) -> None:
    """Soft-delete an environment (status='archived').

    Requires Owner or Maintainer role.
    """
    await _assert_project_access(
        session, project_id, actor_id, ("owner", "maintainer")
    )
    repo = EnvironmentRepository(session)
    env = await repo.get_by_id_and_project(environment_id, project_id)
    if env is None or env.status == "archived":
        raise ResourceNotFoundError("Environment not found.")

    env.status = "archived"
    session.add(env)

    await audit_service.record(
        session,
        event_type=AuditEventType.ENVIRONMENT_MODIFIED,
        actor_id=actor_id,
        project_id=project_id,
        resource_type="environment",
        resource_id=env.id,
        ip_address=ip_address,
        metadata={"action": "archived", "status": "archived"},
    )

    await session.commit()
    logger.info("Environment archived: %s in project %s", env.id, project_id)


async def get_environment(
    session: AsyncSession,
    *,
    project_id: str,
    environment_id: str,
    actor_id: str,
) -> Environment:
    """Retrieve a single active environment."""
    await _assert_project_access(
        session, project_id, actor_id, ("owner", "maintainer", "viewer")
    )
    repo = EnvironmentRepository(session)
    env = await repo.get_by_id_and_project(environment_id, project_id)
    if env is None or env.status == "archived":
        raise ResourceNotFoundError("Environment not found.")
    return env


async def update_environment(
    session: AsyncSession,
    *,
    project_id: str,
    environment_id: str,
    actor_id: str,
    update_data: dict[str, Any],
    ip_address: str | None = None,
) -> Environment:
    """Update fields on an existing environment or soft-delete (status='archived').

    Requires Owner or Maintainer role.
    """
    await _assert_project_access(
        session, project_id, actor_id, ("owner", "maintainer")
    )
    repo = EnvironmentRepository(session)
    env = await repo.get_by_id_and_project(environment_id, project_id)
    if env is None or env.status == "archived":
        raise ResourceNotFoundError("Environment not found.")

    filtered_updates = {
        k: v for k, v in update_data.items() if k in SUPPORTED_UPDATE_FIELDS
    }
    if not filtered_updates:
        return env

    secret_refs_updated = "secret_refs" in filtered_updates
    status_changed = "status" in filtered_updates and filtered_updates["status"] != env.status

    for field, val in filtered_updates.items():
        setattr(env, field, val)

    session.add(env)

    # Audit logging — never record raw secret values
    audit_metadata: dict[str, Any] = {
        "updated_fields": sorted(list(filtered_updates.keys())),
    }
    if secret_refs_updated and env.secret_refs:
        audit_metadata["secret_ref_keys"] = sorted(list(env.secret_refs.keys()))

    await audit_service.record(
        session,
        event_type=AuditEventType.ENVIRONMENT_MODIFIED,
        actor_id=actor_id,
        project_id=project_id,
        resource_type="environment",
        resource_id=env.id,
        ip_address=ip_address,
        metadata=audit_metadata,
    )

    if secret_refs_updated:
        await audit_service.record(
            session,
            event_type=AuditEventType.SECRET_REFERENCE_UPDATED,
            actor_id=actor_id,
            project_id=project_id,
            resource_type="environment",
            resource_id=env.id,
            ip_address=ip_address,
            metadata={
                "secret_ref_keys": sorted(list(env.secret_refs.keys()))
                if env.secret_refs
                else []
            },
        )

    await session.commit()
    await session.refresh(env)
    logger.info("Environment updated: %s in project %s", env.id, project_id)
    return env
