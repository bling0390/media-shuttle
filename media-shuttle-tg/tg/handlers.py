from __future__ import annotations

from dataclasses import dataclass

from .api_client import ApiClient


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

    def on_monitor_command(self) -> dict:
        return self.api.queue_stats()

    def on_worker_command(self, worker: str, queue: str, concurrency: int) -> dict:
        return self.api.admin_worker(worker=worker, queue=queue, concurrency=concurrency)

    def on_rate_command(self, worker: str, task_type: str, rate_limit: str) -> dict:
        return self.api.admin_rate_limit(worker=worker, task_type=task_type, rate_limit=rate_limit)

    def on_retry_command(self, mode: str = "both") -> dict:
        return self.api.admin_retry(mode=mode)

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
