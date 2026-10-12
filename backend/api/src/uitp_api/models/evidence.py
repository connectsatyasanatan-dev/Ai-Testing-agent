"""EvidenceArtifact model."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from uitp_api.database import Base
from uitp_api.database.base import TimestampMixin, UUIDPrimaryKeyMixin


class EvidenceArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata record for a stored evidence artifact (screenshot, trace, log …).

    id is UUIDv4 — non-guessable — but authorization is still enforced on
    every retrieval. Never expose object_key (raw storage path) to clients
    (SECURITY_AND_AI_SAFETY.md §6).
    """

    __tablename__ = "evidence_artifacts"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("step_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "screenshot", "trace", "request_log", "response_log",
            "console_log", "har",
            name="evidence_type",
        ),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="Internal storage path — never expose to API clients",
    )
    checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 hex digest for integrity verification",
    )
    redaction_status: Mapped[str] = mapped_column(
        Enum("pending", "redacted", "failed", "not_required", name="redaction_status"),
        default="pending",
        nullable=False,
    )
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
