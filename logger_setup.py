# logger_setup.py
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(
    name: str = "dfs",
    level: int | None = None,
    log_file: str = "dfs_debug.log",
) -> logging.Logger:
    """
    Set up a predictable logger:
      - Console output
      - Rotating file output
    Level can be forced via DFS_LOG_LEVEL env var: DEBUG/INFO/WARNING/ERROR
    """
    env_level = (os.getenv("DFS_LOG_LEVEL") or "").strip().upper()
    if level is None:
        if env_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            level = getattr(logging, env_level)
        else:
            level = logging.INFO

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers if user re-runs in same interpreter
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File (rotating)
    fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.DEBUG)  # always keep full debug in file
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.debug("Logging initialized (level=%s, file=%s)", logging.getLevelName(level), log_file)
    return logger
