"""Background execution for async investigations (?async=true). Owner: Sampurna."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from app import config

log = logging.getLogger("alibi.live.jobs")

_pool = ThreadPoolExecutor(max_workers=config.LIVE_BACKGROUND_WORKERS, thread_name_prefix="investigation")


def submit(fn: Callable[[], object]) -> Future:
    """Run fn in the background. The pipeline logs its own failures to the run's history."""
    def wrapped():
        try:
            return fn()
        except Exception:  # noqa: BLE001 - already recorded as a "failed" event by the pipeline
            log.exception("background investigation failed")
            return None
    return _pool.submit(wrapped)
