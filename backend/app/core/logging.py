"""Structured-ish logging configuration."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Quieten noisy libraries
    for noisy in ("httpx", "httpcore", "apscheduler", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
