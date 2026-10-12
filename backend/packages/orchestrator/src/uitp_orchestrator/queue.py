"""
uitp_orchestrator.queue
=======================
Redis / RQ queue integration for durable background test execution.

Responsibilities:
  - Transport work reliably between API and worker processes.
  - Wrap connection and enqueue operations with graceful error handling.
  - Ensure API process never leaves runs stuck in 'queued' on transport failure.
  - Support in-memory mock mode for unit tests without live Redis dependencies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import redis
from rq import Queue
from rq.job import Job

from uitp_orchestrator.exceptions import QueueError, QueueUnavailableError

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_NAME = "uitp_runs"
DEFAULT_JOB_TIMEOUT_SECONDS = 600  # 10 minutes
DEFAULT_RESULT_TTL_SECONDS = 86400  # 24 hours


@dataclass
class RunJobPayload:
    """Descriptor for a background run execution job."""

    run_id: str
    func: str = "uitp_worker_runner.execution.execute_run_job"
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS
    meta: dict[str, Any] | None = None


class RunQueue:
    """Manages enqueueing and status checking of test runs via Redis/RQ."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_name: str = DEFAULT_QUEUE_NAME,
        redis_client: redis.Redis | None = None,
        is_mock: bool = False,
    ) -> None:
        self.redis_url = redis_url
        self.queue_name = queue_name
        self.is_mock = is_mock
        self._mock_jobs: dict[str, dict[str, Any]] = {}

        if is_mock:
            self._redis = None
            self._queue = None
        elif redis_client is not None:
            self._redis = redis_client
            self._queue = Queue(name=self.queue_name, connection=self._redis)
        else:
            try:
                self._redis = redis.Redis.from_url(redis_url)
                self._queue = Queue(name=self.queue_name, connection=self._redis)
            except Exception as exc:
                logger.warning("Failed to initialize Redis connection from URL '%s': %s", redis_url, exc)
                self._redis = None
                self._queue = None

    def is_connected(self) -> bool:
        """Check if Redis connection is alive."""
        if self.is_mock:
            return True
        if self._redis is None:
            return False
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    def enqueue_run(
        self,
        run_id: str,
        func: Callable[..., Any] | str = "uitp_worker_runner.execution.execute_run_job",
        timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Enqueue a test execution job into the background queue.

        Args:
            run_id: UUIDv4 of the run to execute.
            func: Import path or callable for the worker job.
            timeout_seconds: Execution deadline before RQ marks job timed out.
            meta: Optional diagnostic metadata.

        Returns:
            job_id string.

        Raises:
            QueueUnavailableError: If Redis is unreachable.
            QueueError: If enqueue fails.
        """
        job_id = f"run:{run_id}"

        if self.is_mock:
            self._mock_jobs[job_id] = {
                "run_id": run_id,
                "status": "queued",
                "meta": meta or {},
            }
            logger.info("[MockQueue] Enqueued run job %s", job_id)
            return job_id

        if self._queue is None or self._redis is None:
            raise QueueUnavailableError("Queue is not connected to a live Redis instance.")

        try:
            job = self._queue.enqueue(
                func,
                run_id=run_id,
                job_id=job_id,
                job_timeout=timeout_seconds,
                result_ttl=DEFAULT_RESULT_TTL_SECONDS,
                meta=meta or {},
            )
            logger.info("Enqueued run %s to queue '%s' (job_id: %s)", run_id, self.queue_name, job.id)
            return job.id
        except redis.ConnectionError as conn_err:
            logger.error("Redis connection failed while enqueuing run %s: %s", run_id, conn_err)
            raise QueueUnavailableError(f"Redis connection error: {conn_err}") from conn_err
        except Exception as exc:
            logger.exception("Failed to enqueue run %s to queue '%s': %s", run_id, self.queue_name, exc)
            raise QueueError(f"Failed to enqueue run: {exc}") from exc

    def get_job_status(self, job_id: str) -> str | None:
        """Query the status of an RQ job (queued, started, finished, failed)."""
        if self.is_mock:
            job_info = self._mock_jobs.get(job_id)
            return job_info["status"] if job_info else None

        if self._redis is None:
            return None

        try:
            job = Job.fetch(job_id, connection=self._redis)
            return job.get_status()
        except Exception:
            return None

    def get_queue_length(self) -> int:
        """Return the count of jobs currently waiting in the queue."""
        if self.is_mock:
            return len(self._mock_jobs)
        if self._queue is None:
            return 0
        try:
            return len(self._queue)
        except Exception:
            return 0
