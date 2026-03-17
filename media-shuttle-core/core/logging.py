from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from loguru import logger as _loguru_logger
except Exception:  # pragma: no cover
    _loguru_logger = None


_LOGGING_CONFIGURED = False
_STD_LOGGER = logging.getLogger("media-shuttle-core")


def _today_log_name() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d.log")


def _resolve_log_dir() -> Path:
    configured = os.getenv("MEDIA_SHUTTLE_CORE_LOG_DIR", "/var/log/media-shuttle-core").strip() or "/var/log/media-shuttle-core"
    primary = Path(configured)
    try:
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    except Exception:
        fallback = Path("/tmp/media-shuttle-core-logs")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if _loguru_logger is None:
            return
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except Exception:
            level = record.levelno
        _loguru_logger.opt(exception=record.exc_info).log(level, record.getMessage())


def _setup_std_logging(log_dir: Path, level: str):
    if _STD_LOGGER.handlers:
        return _STD_LOGGER

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_dir / _today_log_name(), encoding="utf-8")
    file_handler.setFormatter(formatter)

    _STD_LOGGER.handlers = [stdout_handler, file_handler]
    _STD_LOGGER.setLevel(getattr(logging, level, logging.INFO))
    _STD_LOGGER.propagate = False
    return _STD_LOGGER


def setup_logging():
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return _loguru_logger if _loguru_logger is not None else _STD_LOGGER

    log_dir = _resolve_log_dir()
    level = os.getenv("MEDIA_SHUTTLE_CORE_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    retention = os.getenv("MEDIA_SHUTTLE_CORE_LOG_RETENTION", "14 days").strip() or "14 days"

    if _loguru_logger is None:
        logger = _setup_std_logging(log_dir, level)
        logger.info("core logging configured without loguru fallback")
        _LOGGING_CONFIGURED = True
        return logger

    _loguru_logger.remove()
    _loguru_logger.add(
        sys.stdout,
        level=level,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    _loguru_logger.add(
        log_dir / "{time:YYYY-MM-DD}.log",
        level=level,
        rotation="00:00",
        retention=retention,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )

    intercept_handler = _InterceptHandler()
    for name in ("celery", "celery.app.trace", "celery.worker", "kombu", "amqp", "pyrogram"):
        target = logging.getLogger(name)
        target.handlers = [intercept_handler]
        target.propagate = False

    root = logging.getLogger()
    root.handlers = [intercept_handler]
    root.setLevel(logging.INFO)

    _LOGGING_CONFIGURED = True
    _loguru_logger.info(f"core logging configured log_dir={log_dir}")
    return _loguru_logger
