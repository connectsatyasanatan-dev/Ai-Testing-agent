"""AuditEvent model — immutable, append-only audit trail."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from uitp_api.database import Base
from uitp_api.database.base import UUIDPrimaryKeyMixin

# All 15 required event types from ARCHITECTURE.md §5a.
# Adding new types requires a new migration.
_AUDIT_EVENT_TYPES = (
    "user_login",
    "user_login_failed",
    "role_change",
    "project_created",
    "project_archived",
    "environment_created",
    "environment_modified",
    "secret_reference_updated",
    "plan_created",
    "plan_approved",
    "plan_archived",
    "run_created",
    "run_cancelled",
    "policy_blocked",
    "artifact_accessed",
)


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable audit trail record.

    - Never update or delete audit events.
    - metadata_json must be pre-sanitised before storage — never store
      secrets, passwords, or credential values.
    - metadata_redacted=True means metadata was modified before storage.
    """

    __tablename__ = "audit_events"

    actor_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="NULL for system-generated events",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        Enum(*_AUDIT_EVENT_TYPES, name="audit_event_type"),
        nullable=False,
        index=True,
    )
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Pre-sanitised context — no secrets allowed",
    )
    metadata_redacted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent type={self.event_type!r} "
            f"actor={self.actor_id!r} ts={self.timestamp!r}>"
        )
