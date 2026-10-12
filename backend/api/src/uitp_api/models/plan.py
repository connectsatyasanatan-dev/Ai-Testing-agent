"""TestPlan and TestStep models."""
from __future__ import annotations

from sqlalchemy import Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from sqlalchemy import DateTime
from datetime import datetime


class TestPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A versioned, declarative test plan.

    version is a monotonically increasing integer per plan id.
    status lifecycle: draft → approved → archived (soft-delete only).
    requires_approval is always True — AI plans cannot bypass the gate.
    """

    __tablename__ = "test_plans"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="Root plan identifier grouping versions of the same plan",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("draft", "approved", "archived", name="plan_status"),
        default="draft",
        nullable=False,
        index=True,
    )
    source: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="'manual' or 'ai_generated'",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Approval tracking
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    steps: Mapped[list["TestStep"]] = relationship(
        "TestStep",
        back_populates="plan",
        order_by="TestStep.step_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<TestPlan id={self.id!r} name={self.name!r} "
            f"v={self.version} status={self.status!r}>"
        )


class TestStep(UUIDPrimaryKeyMixin, Base):
    """An individual step within a TestPlan version.

    idempotency_class is authoritative — never infer from HTTP method alone
    (ARCHITECTURE.md §5b).
    """

    __tablename__ = "test_steps"

    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("test_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Stable user-visible step identifier within the plan",
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assertions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    retry_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_class: Mapped[str] = mapped_column(
        Enum("safe", "idempotent", "non_idempotent", "unknown", name="idempotency_class"),
        default="unknown",
        nullable=False,
    )
    risk_level: Mapped[str] = mapped_column(
        Enum("low", "medium", "high", "critical", name="risk_level"),
        default="low",
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship
    plan: Mapped["TestPlan"] = relationship("TestPlan", back_populates="steps")
