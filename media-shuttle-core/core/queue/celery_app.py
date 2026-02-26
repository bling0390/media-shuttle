from __future__ import annotations

import os


def build_celery_app():
    """Create Celery app with JSON-only payloads.

    Celery is optional at development time. If celery is unavailable,
    returns None so other layers can run in dry mode.
    """
    try:
        from celery import Celery
    except Exception:
        return None

    redis_url = os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0")
    app = Celery("media-shuttle-core", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
    )
    return app
