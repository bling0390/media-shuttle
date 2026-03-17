from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


try:
    from loguru import logger
except Exception as exc:  # pragma: no cover
    raise RuntimeError("loguru is required to run media-shuttle-api logging") from exc


_LOGGING_CONFIGURED = False


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def setup_logging():
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return logger

    log_dir = Path(os.getenv("MEDIA_SHUTTLE_API_LOG_DIR", "/var/log/media-shuttle-api"))
    log_dir.mkdir(parents=True, exist_ok=True)

    level = os.getenv("MEDIA_SHUTTLE_API_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    rotation = os.getenv("MEDIA_SHUTTLE_API_LOG_ROTATION", "100 MB").strip() or "100 MB"
    retention = os.getenv("MEDIA_SHUTTLE_API_LOG_RETENTION", "7 days").strip() or "7 days"

    logger.remove()
    logger.add(
        sys.stdout,
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        log_dir / "api.log",
        level=level,
        rotation=rotation,
        retention=retention,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )

    intercept_handler = _InterceptHandler()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target = logging.getLogger(name)
        target.handlers = [intercept_handler]
        target.propagate = False

    root = logging.getLogger()
    root.handlers = [intercept_handler]
    root.setLevel(logging.INFO)

    _LOGGING_CONFIGURED = True
    logger.info("api logging configured")
    return logger
