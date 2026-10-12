"""Project and ProjectMembership models."""
from __future__ import annotations

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class ProjectRole(str):
    OWNER = "owner"
    MAINTAINER = "maintainer"
    VIEWER = "viewer"


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A test project — the top-level organisational unit.

    Soft-deleted only: status set to 'archived', never hard-deleted
    (would orphan run history). See ARCHITECTURE.md §5.
    """

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="project_status"),
        default="active",
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Relationships
    memberships: Mapped[list["ProjectMembership"]] = relationship(
        "ProjectMembership",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id!r} name={self.name!r} status={self.status!r}>"


class ProjectMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Many-to-many between User and Project with role assignment.

    Unique constraint ensures one membership record per (project, user) pair.
    """

    __tablename__ = "project_memberships"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_user"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum("owner", "maintainer", "viewer", name="project_role"),
        nullable=False,
        default="viewer",
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="memberships")
    user: Mapped["User"] = relationship("User", back_populates="memberships")

    def __repr__(self) -> str:
        return (
            f"<ProjectMembership project={self.project_id!r} "
            f"user={self.user_id!r} role={self.role!r}>"
        )
