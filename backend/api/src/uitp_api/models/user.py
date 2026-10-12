"""User model — identity and authentication."""
from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Platform user.

    password_hash stores an Argon2id digest (ADR-009).
    Never store plain-text passwords; never log password_hash values.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),   # RFC 5321 max email length
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Argon2id hash — never log this value",
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    memberships: Mapped[list["ProjectMembership"]] = relationship(  # noqa: F821
        "ProjectMembership",
        back_populates="user",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"


# Import here to avoid circular references
from uitp_api.models.project import ProjectMembership  # noqa: E402, F401
