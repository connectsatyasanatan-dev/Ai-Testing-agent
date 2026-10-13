"""
uitp_worker_runner.budget
=========================
Per-run evidence artifact size and count quota tracking (Task P3-07).

Prevents malicious or runaway test steps from exhausting evidence disk storage
by enforcing run-level evidence byte quotas and artifact limits.
"""
from __future__ import annotations

import logging

from uitp_policy.exceptions import EvidenceQuotaExceededError

logger = logging.getLogger(__name__)

DEFAULT_MAX_EVIDENCE_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_ARTIFACT_COUNT = 100


class RunEvidenceBudget:
    """Tracks cumulative evidence writes for a single run and enforces quotas."""

    def __init__(
        self,
        run_id: str,
        *,
        max_bytes: int = DEFAULT_MAX_EVIDENCE_BYTES,
        max_artifacts: int = DEFAULT_MAX_ARTIFACT_COUNT,
    ) -> None:
        self.run_id = run_id
        self.max_bytes = max_bytes
        self.max_artifacts = max_artifacts
        self.total_bytes_written: int = 0
        self.artifact_count: int = 0

    def can_accept(self, payload_bytes: int) -> bool:
        """Return True if adding payload_bytes will remain within budget."""
        if self.artifact_count + 1 > self.max_artifacts:
            return False
        if self.total_bytes_written + payload_bytes > self.max_bytes:
            return False
        return True

    def record_artifact(self, payload_bytes: int) -> None:
        """Record artifact write and enforce limits.

        Raises:
            EvidenceQuotaExceededError: if quota is exceeded.
        """
        if self.artifact_count + 1 > self.max_artifacts:
            raise EvidenceQuotaExceededError(
                f"Run {self.run_id} reached maximum artifact count limit of {self.max_artifacts}."
            )
        if self.total_bytes_written + payload_bytes > self.max_bytes:
            raise EvidenceQuotaExceededError(
                f"Run {self.run_id} exceeded evidence size quota: "
                f"attempted to write {payload_bytes} bytes with {self.total_bytes_written} bytes "
                f"already recorded (quota: {self.max_bytes} bytes)."
            )

        self.total_bytes_written += payload_bytes
        self.artifact_count += 1
        logger.debug(
            "Recorded artifact for run %s (%d bytes). Cumulative: %d/%d bytes, %d/%d artifacts",
            self.run_id,
            payload_bytes,
            self.total_bytes_written,
            self.max_bytes,
            self.artifact_count,
            self.max_artifacts,
        )
