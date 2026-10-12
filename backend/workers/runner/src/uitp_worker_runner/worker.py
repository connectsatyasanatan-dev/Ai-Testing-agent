"""
uitp_worker_runner.worker
=========================
Lifecycle management and execution runner for RQ background workers.

Features:
  - Connects to configured Redis instance.
  - Automatically selects SimpleWorker on Windows and Worker on Linux to avoid fork issues.
  - Registers graceful shutdown hooks for SIGINT / SIGTERM.
  - Supports burst mode for testing and batch drains.
"""
from __future__ import annotations

import logging
import os
import platform
import signal
import sys
from typing import Sequence

import redis
from rq import Queue, SimpleWorker, Worker

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_NAME = "uitp_runs"


def get_worker_class() -> type[Worker]:
    """Return appropriate Worker class depending on OS capabilities.

    Windows does not support os.fork, requiring SimpleWorker.
    """
    if os.name == "nt" or platform.system() == "Windows":
        return SimpleWorker
    return Worker


def start_worker(
    redis_url: str = "redis://localhost:6379/0",
    queue_names: Sequence[str] = (DEFAULT_QUEUE_NAME,),
    worker_name: str | None = None,
    burst: bool = False,
) -> None:
    """Run an RQ worker process listening on the specified queues.

    Args:
        redis_url: Redis connection string.
        queue_names: Queue names to listen on.
        worker_name: Optional explicit name for this worker instance.
        burst: If True, exit as soon as all queues are empty.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing UITP background worker...")
    logger.info("Connecting to Redis at: %s", redis_url)

    try:
        redis_conn = redis.Redis.from_url(redis_url)
        redis_conn.ping()
        logger.info("Successfully connected to Redis.")
    except Exception as exc:
        logger.error("Failed to connect to Redis at %s: %s", redis_url, exc)
        sys.exit(1)

    queues = [Queue(name, connection=redis_conn) for name in queue_names]
    worker_cls = get_worker_class()

    worker = worker_cls(
        queues,
        connection=redis_conn,
        name=worker_name,
    )

    logger.info(
        "Worker '%s' (%s) listening on queues: %s (burst=%s)",
        worker.name,
        worker_cls.__name__,
        list(queue_names),
        burst,
    )

    # Register graceful shutdown
    def _handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Received termination signal %s. Shutting down worker gracefully...", signum)
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _handle_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_shutdown)
    except Exception as sig_err:
        logger.debug("Signal handler registration note: %s", sig_err)

    try:
        worker.work(burst=burst)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received; worker stopping.")
    except Exception as exc:
        logger.exception("Worker terminated with error: %s", exc)
        sys.exit(1)

    logger.info("Worker finished successfully.")
