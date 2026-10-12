"""AdapterRegistration model — records which adapters are available."""
from __future__ import annotations

from sqlalchemy import Enum, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class AdapterRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registry of known adapter types and their capability documents.

    capability_document is a JSON object matching AdapterCapabilities schema
    from packages/contracts — persisted here so runs can be reproduced
    against the exact capability snapshot at execution time.
    """

    __tablename__ = "adapter_registrations"

    adapter_type: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_document: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "disabled", name="adapter_status"),
        default="active",
        nullable=False,
        index=True,
    )
