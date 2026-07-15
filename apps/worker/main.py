"""RQ worker bootstrap.

Long-running jobs (ingestion, training, backtest, evaluation) run here.
Never do heavy work in the API request thread.
"""
from __future__ import annotations

import os
import sys

from redis import Redis
from rq import Connection, Queue, Worker

from packages.common import configure_logging, get_logger

logger = get_logger("worker")

DEFAULT_QUEUES = ["ingestion", "features", "training", "backtest", "evaluation", "default"]


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queues = (argv or DEFAULT_QUEUES)
    conn = Redis.from_url(redis_url)
    logger.info("worker.starting", redis_url=redis_url, queues=queues)
    with Connection(conn):
        w = Worker([Queue(q) for q in queues])
        w.work(with_scheduler=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:] or None))
