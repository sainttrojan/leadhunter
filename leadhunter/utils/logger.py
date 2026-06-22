"""
Structured logging for LeadHunter.

Writes to both console and a rotating daily log file under <base>/logs/.
Call `get_logger(__name__)` from every module — it auto-creates the log
dir and attaches the handlers exactly once.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from ..config import get_config

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_HANDLERS: list[logging.Handler] = []
_configured = False


def _ensure_handlers() -> None:
    global _configured
    if _configured:
        return
    cfg = get_config()
    try:
        os.makedirs(cfg.logs_dir, exist_ok=True)
    except Exception:
        pass

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    _HANDLERS.append(console)

    try:
        file_handler = TimedRotatingFileHandler(
            os.path.join(cfg.logs_dir, "leadhunter.log"),
            when="midnight", backupCount=14, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        _HANDLERS.append(file_handler)
    except Exception:
        # File logging is best-effort; console-only is acceptable.
        pass

    _configured = True


def get_logger(name: str = "leadhunter") -> logging.Logger:
    _ensure_handlers()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        for h in _HANDLERS:
            logger.addHandler(h)
    logger.propagate = False
    return logger
