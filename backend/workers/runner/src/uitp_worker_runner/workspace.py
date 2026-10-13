"""
uitp_worker_runner.workspace
============================
Per-run temporary filesystem workspace and cleanup management (Task P3-07).

Responsibilities:
  - Allocates an isolated, dedicated temporary directory for each run attempt.
  - Enforces storage quota boundaries (preventing disk space exhaustion).
  - Guarantees complete cleanup upon run completion, timeout, cancellation, or crash.
  - Provides safe subdirectory helpers for future browser profiles, traces, and scratch data.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from types import TracebackType

from uitp_policy.exceptions import StorageQuotaExceededError

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


class ExecutionWorkspace:
    """Manages an isolated temporary directory lifecycle for a test run."""

    def __init__(
        self,
        run_id: str,
        *,
        max_bytes: int = DEFAULT_WORKSPACE_MAX_BYTES,
        base_dir: str | Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.max_bytes = max_bytes
        self.base_dir = Path(base_dir) if base_dir else Path(tempfile.gettempdir())
        self.path: Path = self.base_dir / f"uitp_run_{run_id}"
        self._is_active = False

    def enter(self) -> ExecutionWorkspace:
        """Create and initialize the run's isolated workspace directory."""
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            self._is_active = True
            logger.debug("Initialized execution workspace for run %s at %s", self.run_id, self.path)
            return self
        except Exception as exc:
            logger.error("Failed to create workspace directory at %s: %s", self.path, exc)
            raise

    def exit(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        """Clean up and remove the entire workspace directory tree."""
        if self._is_active and self.path.exists():
            try:
                shutil.rmtree(self.path, ignore_errors=True)
                logger.debug("Successfully cleaned up workspace for run %s", self.run_id)
            except Exception as exc:
                logger.warning("Error during workspace cleanup for run %s (%s): %s", self.run_id, self.path, exc)
            finally:
                self._is_active = False

    def __enter__(self) -> ExecutionWorkspace:
        return self.enter()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.exit(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> ExecutionWorkspace:
        return self.enter()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.exit(exc_type, exc_val, exc_tb)

    def get_used_bytes(self) -> int:
        """Calculate total bytes of all files currently stored in this workspace."""
        if not self.path.exists():
            return 0
        total_size = 0
        for dirpath, _, filenames in os.walk(self.path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        return total_size

    def check_storage_quota(self) -> int:
        """Verify storage does not exceed max_bytes quota.

        Raises:
            StorageQuotaExceededError: if used bytes exceed max_bytes.
        """
        used = self.get_used_bytes()
        if used > self.max_bytes:
            raise StorageQuotaExceededError(
                f"Run {self.run_id} workspace exceeded maximum temporary storage "
                f"quota of {self.max_bytes} bytes (currently using {used} bytes)."
            )
        return used

    def create_subdirectory(self, name: str) -> Path:
        """Create a safe subdirectory inside the workspace (e.g. for browser profiles)."""
        clean_name = os.path.basename(name)
        sub = self.path / clean_name
        sub.mkdir(parents=True, exist_ok=True)
        return sub
