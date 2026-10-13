"""ApiToken model — programmatic credentials for CI/non-browser clients (ADR-009, P4-00)."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class ApiToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Opaque API token issued to users for headless automation.

    Tokens are stored as SHA-256 hashes. Raw tokens are never persisted.
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        comment="SHA-256 digest of opaque token",
    )
    token_prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="Visible prefix for UI display (e.g. uitp_tok_...)",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(  # noqa: F821
        "User",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<ApiToken id={self.id!r} prefix={self.token_prefix!r} revoked={self.is_revoked}>"
