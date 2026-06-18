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

try:
    # pyrogram 2.0+ uses an enum for ``parse_mode``; older
    # versions accepted a plain string. We import lazily so
    # the notifier module can be imported by the unit tests
    # without the heavy pyrogram dependency installed.
    from pyrogram.enums import ParseMode
except Exception:  # pragma: no cover
    ParseMode = None

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


def _format_duration(seconds: int) -> str:
    """Render a duration in seconds as a short human string.

    Examples: 5 -> ``5s``; 75 -> ``1m15s``; 3725 -> ``1h2m5s``.
    Returns ``""`` for non-positive values so the caller can
    drop the line entirely.
    """
    if seconds <= 0:
        return ""
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def _truncate(s: str, max_len: int) -> str:
    """Clamp a string to ``max_len`` characters, ellipsizing the tail.

    The downstream ``<pre>`` block in the box layout relies
    on a consistent column width; an untruncated filename
    blows the right margin. Truncation is character-based
    (not bytes) to behave sanely for non-ASCII names.
    """
    if len(s) <= max_len:
        return s
    if max_len <= 1:
        return s[:max_len]
    return s[: max_len - 1] + "…"


def _format_notification(event: dict[str, Any]) -> str:
    """Render a task.completed event into a Telegram message.

    Three styles are supported, picked by the env var
    ``TG_NOTIFY_STYLE`` (default ``box``):

    * ``box`` (default) — monospace box with key / value
      columns. Best for at-a-glance scanning of long
      filenames and sizes. Uses ``parse_mode=html`` on
      the tg side so the box renders as a fixed-width
      block; falls back to plain text if the renderer
      strips HTML.
    * ``kv`` — list of ``key: value`` lines, no box
      border. Slightly more compact; reads well in any
      client.
    * ``inline`` — the original ``<name> — <size>`` form,
      kept for operators that prefer one-liner notifications.

    All three styles share the same field set so the
    payload contract is unchanged: the operator can
    switch styles per-deployment without touching the
    core / api layer.
    """
    name = str(event.get("file_name") or "(unknown)")
    size = int(event.get("size_bytes") or 0)
    source = str(event.get("source_site") or "")
    location = str(event.get("location") or "")
    duration = _format_duration(int(event.get("duration_seconds") or 0))
    # ``ok`` was added when per-file notifications were wired
    # up in core/queue/tasks.py::process_upload_result_logic.
    # Treat missing as success (the legacy finalize summary
    # never set the field, and we want to stay compatible with
    # any pre-existing buffered events on the redis queue).
    ok = event.get("ok", True)
    ok = bool(ok) if not isinstance(ok, str) else ok.strip().lower() not in {
        "false",
        "0",
        "no",
        "fail",
        "failed",
    }
    reason = str(event.get("reason") or "")

    style = os.getenv("TG_NOTIFY_STYLE", "box").strip().lower()
    if style == "inline":
        prefix = "✅" if ok else "❌"
        suffix = f" — {reason}" if not ok and reason else ""
        return f"{prefix} {name} — {_format_bytes(size)}{suffix}"
    if style == "kv":
        return _format_kv(name, size, source, location, duration, ok=ok, reason=reason)
    return _format_box(name, size, source, location, duration, ok=ok, reason=reason)


def _format_kv(
    name: str, size: int, source: str, location: str, duration: str, ok: bool = True, reason: str = ""
) -> str:
    """``key: value`` per line. No border.

    Example (success)::

        状态：✅ 上传成功
        文件名：Hegre_Serena_L_-_Sci-Fi_Cosmic_Climax_Massage_4K.mp4
        文件大小：4.2 GiB
        耗时：1m12s

    Example (failure)::

        状态：❌ 上传失败
        文件名：Hegre_Serena_L_-_Sci-Fi_Cosmic_Climax_Massage_4K.mp4
        文件大小：4.2 GiB
        原因：rclone: connection reset
    """
    status = "✅ 上传成功" if ok else "❌ 上传失败"
    lines = [
        f"状态：{status}",
        f"文件名：{name}",
        f"文件大小：{_format_bytes(size)}",
    ]
    if source:
        lines.append(f"来源：{source}")
    if location:
        lines.append(f"位置：{location}")
    if not ok and reason:
        lines.append(f"原因：{_truncate(reason, 60)}")
    if duration:
        lines.append(f"耗时：{duration}")
    return "\n".join(lines)


def _format_box(
    name: str, size: int, source: str, location: str, duration: str, ok: bool = True, reason: str = ""
) -> str:
    """Monospace table layout, sent as a ``<pre>`` block.

    The widths are picked so the rows read as a two-
    column table inside the ``<pre>`` rendering.
    Telegram renders ``<pre>`` as a fixed-width block,
    so columns line up even with non-ASCII characters
    (with the usual CJK-width caveat).

    The outer box border was dropped because it adds
    visual weight without information — the
    status-separator line (after row 0) is enough to
    mark the header.

    Example (success)::

        状态  ✅ 上传成功
        ──────────────────────────────
        文件名  Hegre_Serena_L...
        大小    4.2 GiB
        来源    bunkrr.su
        位置    rclone://115:/...
        耗时    1m12s

    Example (failure)::

        状态  ❌ 上传失败
        ──────────────────────────────
        文件名  Hegre_Serena_L...
        大小    4.2 GiB
        原因    rclone: connection...
        来源    bunkrr.su
        耗时    1m12s
    """
    status = "✅ 上传成功" if ok else "❌ 上传失败"
    rows: list[tuple[str, str]] = [("状态", status)]
    # Truncate filename to keep the table readable. 36 chars
    # is the sweet spot for a typical phone screen.
    rows.append(("文件名", _truncate(name, 36)))
    rows.append(("大小", _format_bytes(size)))
    if not ok and reason:
        # Show the reason right after the size so the failure
        # cause is the most prominent field for failed rows.
        rows.append(("原因", _truncate(reason, 36)))
    if source:
        rows.append(("来源", source))
    if location:
        rows.append(("位置", _truncate(location, 36)))
    if duration:
        rows.append(("耗时", duration))

    label_width = max(len(label) for label, _ in rows)
    value_width = max(len(value) for _, value in rows)
    # The interior of a row is ``<label>  <value>`` —
    # label + 2 inner spaces + value. Total interior
    # width is what the separator line is sized to match.
    interior = label_width + 2 + value_width
    separator = "─" * interior

    out: list[str] = []
    for idx, (label, value) in enumerate(rows):
        line = f"{label.ljust(label_width)}  {value.ljust(value_width)}"
        out.append(line)
        if idx == 0:
            # Visual separator after the status line so
            # the status reads as a header and the rest
            # of the rows look like a data table.
            out.append(separator)
    body = "\n".join(out)
    # ``<pre>`` forces monospace in tg so the columns
    # actually line up. ``<code>`` would also work but
    # ``<pre>`` is the more common pattern for pre-
    # formatted text. We escape HTML special chars in
    # the body just in case a future filename contains
    # one (most don't).
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre>{escaped}</pre>"


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
        # Box layout wraps the body in a ``<pre>`` block so
        # columns line up. Other layouts are plain text.
        # Falling back from HTML to plain text on a render
        # error would silently produce ugly output, so we
        # detect the box style once and pick ``parse_mode``
        # accordingly. ``pyrogram.enums.ParseMode.HTML`` is
        # the typed value expected by ``send_message`` on
        # pyrogram 2.0+; older versions accepted the string
        # ``"html"`` but the enum is the documented form.
        parse_mode = (
            ParseMode.HTML
            if (ParseMode is not None and text.lstrip().startswith("<pre>"))
            else None
        )
        try:
            # pyrogram 2.0.x exposes ``send_message`` as a sync
            # wrapper that internally drives its own event loop.
            # To call it from this background thread we wrap it in
            # a fresh coroutine and schedule that onto the bot's
            # loop via ``run_coroutine_threadsafe``. ``send_message``
            # itself is the coroutine-under-the-hood and is what
            # the wrapper awaits, so this is the documented path.
            async def _send() -> None:
                if parse_mode is not None:
                    await self._app.send_message(chat_id, text, parse_mode=parse_mode)
                else:
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
