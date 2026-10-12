"""Environment model — target endpoint configuration per project."""
from __future__ import annotations

from sqlalchemy import Enum, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class Environment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named test environment within a project.

    - base_url: the root URL of the target application.
    - allowlist: JSON list of permitted host:port strings (SSRF defence).
    - secret_refs: JSON object of {name: secret_manager_reference}.
      NEVER store actual secret values — only references.
    - Soft-deleted only (status='archived').
    """

    __tablename__ = "environments"

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    allowlist: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Permitted hosts/ports for SSRF defence — JSON list of strings",
    )
    secret_refs: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Secret manager references only — never plaintext values",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="environment_status"),
        default="active",
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<Environment id={self.id!r} name={self.name!r} project={self.project_id!r}>"
