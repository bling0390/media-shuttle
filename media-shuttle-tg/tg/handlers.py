from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .api_client import ApiClient

logger = logging.getLogger("media_shuttle.tg.handlers")


@dataclass
class LeechSession:
    requester_id: str
    target: str = "RCLONE"
    destination: str = "/"


def _format_bytes(n: int) -> str:
    """Render a byte count using binary units an operator
    can read at a glance: ``1.0 KiB``, ``12.3 MiB``,
    ``2.4 GiB``. Zero renders as ``0 B`` (not ``0.0 B``)
    so an empty cleanup reply stays compact.
    """
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


def format_cleanup_reply(payload: dict | None) -> str:
    """Turn the api cleanup summary into a short Telegram
    message.

    Layout:

    * header line with mode (dry-run vs real) and root
    * one-liner with scanned/removed/skipped counters and
      the freed byte count
    * at most 20 item lines so a chat with a large
      download root does not flood the operator's history
    * a ``(truncated)`` line if more items were present
    """
    if not payload:
        return "cleanup failed: empty response"
    if not payload.get("accepted", False):
        return (
            "cleanup failed: "
            f"reason={payload.get('reason', 'unknown')}"
            + (f" error={payload['error']}" if payload.get("error") else "")
        )

    root = payload.get("root", "?")
    dry = bool(payload.get("dry_run", False))
    scanned = int(payload.get("scanned", 0))
    removed = int(payload.get("removed", 0))
    skipped = int(payload.get("skipped", 0))
    freed = int(payload.get("freed_bytes", 0))
    items = payload.get("items", []) or []

    mode = "DRY-RUN" if dry else "CLEANUP"
    lines = [
        f"[{mode}] root={root}",
        (
            f"scanned={scanned} removed={removed} "
            f"skipped={skipped} freed={_format_bytes(freed)}"
        ),
    ]

    if not items:
        lines.append("(no items)")
        return "\n".join(lines)

    cap = 20
    for it in items[:cap]:
        kind = it.get("kind", "?")
        size = _format_bytes(int(it.get("size_bytes", 0)))
        path = it.get("path", "")
        # Trim the prefix to the download-root name to keep
        # the line short. Falls back to the full path if
        # the prefix is missing.
        try:
            short = path.split(root.rstrip("/"), 1)[-1].lstrip("/")
            if not short:
                short = path
        except Exception:
            short = path
        marker = "x" if it.get("removed") else "-"
        reason = it.get("reason")
        tail = f" [{reason}]" if reason else ""
        lines.append(f"  {marker} {kind:6s} {size:>9s}  {short}{tail}")

    if len(items) > cap:
        lines.append(f"  ... ({len(items) - cap} more truncated)")
    return "\n".join(lines)


class TgHandlers:
    def __init__(self, api_client: ApiClient) -> None:
        self.api = api_client

    def on_leech_command(
        self, requester_id: str, url: str, target: str, destination: str | None = None
    ) -> dict:
        # For RCLONE the api defaults destination to ``115:/`` so the
        # final path is ``115:/<date>/<folder>/<file>``. Callers that
        # want a custom subpath can still pass ``destination=...``.
        return self.api.create_parse_task(
            url=url,
            requester_id=requester_id,
            target=target,
            destination=destination,
        )

    def on_leech_forum_command(
        self,
        requester_id: str,
        url: str,
        target: str = "RCLONE",
        destination: str | None = None,
        max_pages: int | None = None,
    ) -> dict:
        """Submit a forum thread for extraction.

        The forum task is dispatched asynchronously (B-scheme
        from the design notes): the bot hands the event to
        the api, the api publishes it to
        ``media_shuttle:task_forum_thread``, and the core
        worker walks the thread in the background. The
        operator gets a task_id back immediately and the
        per-file upload notifications arrive later as the
        fan-out ``parse_link`` events complete.
        """
        return self.api.create_forum_task(
            url=url,
            requester_id=requester_id,
            target=target,
            destination=destination,
            max_pages=max_pages,
        )

    def on_monitor_command(self) -> str:
        """Backing handler for ``/monitor``.

        Returns a pre-formatted string rather than the raw
        ``dict`` so the bot reply reads as a small table
        rather than ``{'parse': 0, 'download': 1, ...}``.
        The bot itself does ``await message.reply(...)`` and
        doesn't know how to lay out the keys, so the layout
        lives here. We surface both task-level and source-
        level counts because the source-level view is the
        useful one for multi-file albums — a 247-file
        album shows as ``download_sources: 247`` rather
        than ``download: 1``.
        """
        stat = self.api.queue_stats()
        # Defensive defaults: an older api (or a mocked
        # client in tests) might not return the new keys.
        rows = [
            ("parse",            stat.get("parse", 0)),
            ("download tasks",   stat.get("download", 0)),
            ("download sources", stat.get("download_sources", 0)),
            ("upload tasks",     stat.get("upload", 0)),
            ("upload sources",   stat.get("upload_sources", 0)),
        ]
        label_width = max(len(label) for label, _ in rows)
        lines = [f"📊 monitor", ""]
        for label, value in rows:
            lines.append(f"  {label.ljust(label_width)}  {value}")
        return "\n".join(lines)

    def on_worker_command(self, worker: str, queue: str, concurrency: int) -> dict:
        return self.api.admin_worker(worker=worker, queue=queue, concurrency=concurrency)

    def on_rate_command(self, worker: str, task_type: str, rate_limit: str) -> dict:
        return self.api.admin_rate_limit(worker=worker, task_type=task_type, rate_limit=rate_limit)

    def on_retry_command(self, mode: str = "both") -> dict:
        return self.api.admin_retry(mode=mode)

    def on_retry_task_command(self, task_id: str, requester_id: str) -> dict:
        """Re-queue a single failed task for the operator
        who owns it.

        Backs the inline ``🔁 重试`` button on the
        Telegram failure notification. ``task_id`` and
        ``requester_id`` both come from the callback
        payload (``retry_<phase>:<task_id>`` plus the
        original task.completed event's requester_id
        that the bot already has in scope).

        Returns a small dict with the keys the
        CallbackQueryHandler expects:

        * ``ok`` — True if the api accepted the retry
        * ``code`` — short string for the bot to render
          ("accepted" / "not_found" / "already_retried" /
          "missing_requester")
        * ``message`` — Chinese toast shown to the
          operator via ``answerCallbackQuery``
        * ``task_id`` — echoed back so the bot can edit
          the original failure notification
        * ``task_type`` — ``"parse_link"`` /
          ``"parse_forum_thread"`` so the bot can show
          the right follow-up message ("重试中…" vs
          "重新抓取中…"). Optional; only present on
          accepted retries.

        We translate ``httpx.HTTPStatusError`` into a
        structured code instead of letting it bubble:
        the callback handler lives in a bot event loop
        and an uncaught exception would just log
        "Update is handled" without telling the operator
        anything useful.
        """
        task_key = (task_id or "").strip()
        requester_key = (requester_id or "").strip()
        if not task_key:
            return {
                "ok": False,
                "code": "missing_task_id",
                "message": "任务 ID 丢失",
                "task_id": "",
            }
        if not requester_key:
            return {
                "ok": False,
                "code": "missing_requester",
                "message": "操作者 ID 丢失",
                "task_id": task_key,
            }
        try:
            result = self.api.retry_task(task_id=task_key, requester_id=requester_key)
        except httpx.HTTPStatusError as exc:
            status = int(getattr(exc.response, "status_code", 0) or 0)
            # 404 covers both a real missing task AND a
            # cross-operator mismatch — the api is
            # deliberately indistinguishable for the
            # reasons in main.py.
            if status == 404:
                return {
                    "ok": False,
                    "code": "not_found",
                    "message": "任务不存在或无权操作",
                    "task_id": task_key,
                }
            if status == 409:
                return {
                    "ok": False,
                    "code": "already_retried",
                    "message": "已重试过，请等待任务跑完",
                    "task_id": task_key,
                }
            if status == 403:
                return {
                    "ok": False,
                    "code": "missing_requester",
                    "message": "操作者 ID 丢失",
                    "task_id": task_key,
                }
            logger.warning(
                f"retry_task http error task_id={task_key} status={status} reason={exc}"
            )
            return {
                "ok": False,
                "code": "http_error",
                "message": f"api 错误（HTTP {status}）",
                "task_id": task_key,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"retry_task unexpected error task_id={task_key} reason={exc}")
            return {
                "ok": False,
                "code": "transport_error",
                "message": f"网络错误：{exc}",
                "task_id": task_key,
            }
        task_type = str(result.get("task_type") or "parse_link")
        if task_type == "parse_forum_thread":
            follow_up = "🔁 重新抓取中…"
        else:
            follow_up = "🔁 重试中…"
        return {
            "ok": True,
            "code": "accepted",
            "message": follow_up,
            "task_id": task_key,
            "task_type": task_type,
        }

    def on_setting_command(self, key: str, value: str) -> dict:
        return self.api.admin_setting(key=key, value=value)

    def on_cleanup_command(self, dry_run: bool = False) -> dict:
        """Backing handler for ``/leech cleanup [dry]``.

        Default mode is a real cleanup; pass ``dry_run=True``
        to preview the size and paths that would be removed.
        The api response is returned verbatim so the bot can
        format it directly into a Telegram reply.
        """
        return self.api.cleanup_downloads(dry_run=dry_run)
