"""File logging (Design Section 5.2) - Spec Section 9."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import app_dir

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
ROOT_LOGGER = "dashcam_merger"


def setup_logging(run_id: str, verbose: bool, log_dir: Path | None = None) -> Path:
    """Attach a file handler for this run and return the log file path."""
    directory = log_dir if log_dir is not None else app_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / f"merge_{run_id}.log"

    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    return log_file


def prune_old_logs(log_dir: Path, max_age_days: int = 30) -> int:
    """Delete merge_*.log older than max_age_days. The only delete in the program."""
    assert log_dir.name == "logs", "prune_old_logs may only run inside a 'logs' folder"
    if not log_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for entry in log_dir.glob("merge_*.log"):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:  # a log in use by another run; leave it alone
            continue
    return removed
