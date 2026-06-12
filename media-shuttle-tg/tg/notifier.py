"""Background subscriber that pushes task completion events
from Redis into the operator's Telegram chat.

Architecture
============

``core/queue/tasks.py::process_finalize_task_logic`` publishes
a JSON message to a Redis list (default
``media_shuttle:event_task_completed``) once a task transitions
to ``SUCCEEDED``. The telegram bot runs a daemon thread that
``BRPOP``s that list and forwards each event to the requester
via ``bot.send_message``.

Why a thread and not a separate container
-----------------------------------------

* Low event volume (one message per uploaded file, not per
  chunk), so a single thread is plenty.
* Sharing the pyrogram ``Client`` between the main bot loop
  and a worker thread is fine — pyrogram is async; we just
  schedule the coroutine onto its loop from the worker via
  ``asyncio.run_coroutine_threadsafe``.
* Keeping it in-process means no extra service to deploy or
  healthcheck.

Failure modes
-------------

* Redis unreachable: the BRPOP loop catches the exception,
  sleeps, and retries. Events queued while Redis was down
  remain on the list and are consumed on the next successful
  pop.
* Telegram send fails (network, blocked user, etc.): the
  exception is logged and the event is dropped. The task is
  already ``SUCCEEDED`` in mongo, so a missed notification is
  the only consequence.
* Bot not yet ``/start``-ed by the user: ``send_message`` will
  raise ``PeerIdInvalid``; we log and drop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("media_shuttle.tg.notifier")

# Default key; overridden by MEDIA_SHUTTLE_NOTIFICATION_QUEUE_KEY
# to keep the core and tg sides in lockstep.
_DEFAULT_KEY = "media_shuttle:event_task_completed"


def _queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_NOTIFICATION_QUEUE_KEY", _DEFAULT_KEY)


def _redis_url() -> str:
    return os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0")


def _format_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(n)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if units[idx] == "B":
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def _format_notification(event: dict[str, Any]) -> str:
    """Render the event into a short telegram message.

    Layout (per the operator's request, kept minimal):

        <file_name> — <size>

    Future-proofed: if the spec grows to include more fields
    (duration, source site, target drive), they slot in here
    without touching the subscriber.
    """
    name = str(event.get("file_name") or "(unknown)")
    size = int(event.get("size_bytes") or 0)
    return f"{name} — {_format_bytes(size)}"


class TaskCompletedNotifier:
    """Daemon-thread wrapper that owns the BRPOP loop.

    ``start`` is non-blocking and returns immediately. The
    thread runs until ``stop_event`` is set (or the process
    dies). All pyrogram interactions are scheduled onto the
    main bot's event loop via
    ``asyncio.run_coroutine_threadsafe``.
    """

    def __init__(self, app, loop: asyncio.AbstractEventLoop) -> None:
        self._app = app
        self._loop = loop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="tg-notifier",
            daemon=True,
        )
        self._thread.start()
        logger.info("task completed notifier started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        import redis

        backoff = 1.0
        while not self._stop.is_set():
            try:
                client = redis.Redis.from_url(_redis_url())
                # Cheap readiness check; raises if unreachable.
                client.ping()
                backoff = 1.0
                self._drain(client)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"notifier redis unavailable, retrying in {backoff:.1f}s reason={exc}"
                )
                # sleep in small slices so stop() is responsive
                slept = 0.0
                while slept < backoff and not self._stop.is_set():
                    time.sleep(0.1)
                    slept += 0.1
                backoff = min(backoff * 2.0, 30.0)

    def _drain(self, client) -> None:
        """Block-pop events until ``stop_event`` fires.

        We use a short ``brpop`` timeout so the loop can check
        ``_stop`` periodically without needing a second
        thread to cancel the redis call.
        """
        while not self._stop.is_set():
            try:
                popped = client.brpop(_queue_key(), timeout=1)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"notifier brpop failed, will reconnect reason={exc}")
                return
            if not popped:
                continue
            _, raw = popped
            try:
                event = json.loads(raw)
            except Exception as exc:
                logger.warning(f"notifier dropping malformed event reason={exc} raw={raw!r}")
                continue
            self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        requester_id = str(event.get("requester_id") or "")
        if not requester_id:
            logger.warning("notifier dropping event without requester_id")
            return
        try:
            chat_id = int(requester_id)
        except ValueError:
            logger.warning(f"notifier dropping event with non-numeric requester_id={requester_id!r}")
            return
        text = _format_notification(event)
        try:
            # pyrogram 2.0.x exposes ``send_message`` as a sync
            # wrapper that internally drives its own event loop.
            # To call it from this background thread we wrap it in
            # a fresh coroutine and schedule that onto the bot's
            # loop via ``run_coroutine_threadsafe``. ``send_message``
            # itself is the coroutine-under-the-hood and is what
            # the wrapper awaits, so this is the documented path.
            async def _send() -> None:
                await self._app.send_message(chat_id, text)

            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            future.add_done_callback(self._on_send_done)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"notifier dispatch failed chat_id={chat_id} reason={exc}")

    @staticmethod
    def _on_send_done(fut) -> None:
        try:
            exc = fut.exception()
        except Exception:  # pragma: no cover
            return
        if exc is not None:
            logger.warning(f"notifier send_message raised: {exc}")
