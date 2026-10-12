"""
uitp_worker_runner.cli
======================
Command-line entry point for the background test worker process.

Usage:
    python -m uitp_worker_runner.cli [--redis-url <url>] [--queue <name>] [--burst]
"""
from __future__ import annotations

import argparse
import os
import sys

from uitp_worker_runner.worker import DEFAULT_QUEUE_NAME, start_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UITP Background Test Execution Worker")
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("UITP_REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0")),
        help="Redis connection URL (default: redis://localhost:6379/0 or $UITP_REDIS_URL)",
    )
    parser.add_argument(
        "--queue",
        action="append",
        dest="queues",
        help="Queue name(s) to listen to (can be specified multiple times; default: uitp_runs)",
    )
    parser.add_argument(
        "--worker-id",
        dest="worker_id",
        default=os.environ.get("UITP_WORKER_ID"),
        help="Custom worker instance identifier",
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Run in burst mode: process all pending jobs in queues and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    queues = args.queues if args.queues else [DEFAULT_QUEUE_NAME]
    start_worker(
        redis_url=args.redis_url,
        queue_names=queues,
        worker_name=args.worker_id,
        burst=args.burst,
    )


if __name__ == "__main__":
    main()
