"""Forum dispatcher: turn thread URLs into parse_link tasks.

Public surface used by the celery worker:

* :func:`build_forum_dispatcher` — entry point the worker calls
  with a parsed ``parse_forum_thread`` event.
* :class:`ForumDispatchResult` — outcome the worker persists
  back into the forum task record.

The split between :mod:`forum` (orchestration) and
:mod:`forum.extractors` (per-site parsing) is the same one
used in the existing :mod:`parsers_sites` layout: a small
dispatcher in the providers layer delegates to pluggable
backends, so adding a new forum is a config + a single
new file.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .client import ForumClient, ForumFetchError
from .cookies import DEFAULT_HOST_TO_FORUM_KEY, build_cookie_header_resolver
from .extractors import UnsupportedForumError, select_extractor_class

logger = logging.getLogger(__name__)

DEFAULT_FORUM_THREAD_QUEUE_KEY = "media_shuttle:task_forum_thread"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _normalize_thread_url(url: str) -> str:
    """Strip ``/page-N`` from a thread URL so the dispatcher
    can compute page URLs from a clean base.

    Accepts both ``/threads/<slug>.<id>/`` and
    ``/threads/<slug>.<id>/page-N`` — the latter is
    normalized to the former.

    Implementation note: we split the URL on the first
    ``://`` to preserve the scheme (otherwise the
    ``/page-N`` -> ``/`` substitution can collapse
    ``https://`` into ``https:/``).
    """
    if not url:
        return url
    # Preserve scheme: split, do path-level work, rejoin.
    if "://" in url:
        scheme, _, rest = url.partition("://")
        prefix = scheme + "://"
    else:
        prefix = ""
        rest = url
    cleaned = re.sub(r"/page-\d+(/|$)", "/", rest)
    # Collapse any trailing double slashes that the
    # substitution above might have introduced.
    cleaned = re.sub(r"/+", "/", cleaned)
    if not cleaned.endswith("/"):
        cleaned += "/"
    return prefix + cleaned


@dataclass
class ForumDispatchResult:
    """Outcome of running a forum dispatch.

    Persisted back into the forum task's mongo record by the
    caller (the worker). The fanned-out ``parse_link`` events
    live in :attr:`fanned_out_events` so the caller can publish
    them to ``media_shuttle:task_created``.
    """

    forum_task_id: str
    status: str  # "SUCCEEDED" | "SUCCEEDED_WITH_NO_OUTPUT" | "FAILED"
    message: str
    last_page: int = 0
    pages_walked: int = 0
    extracted_links: int = 0
    fanned_out_count: int = 0
    fanned_out_events: list[dict[str, Any]] = field(default_factory=list)
    last_error: str = ""


def _build_fanout_event(
    *,
    forum_task_id: str,
    url: str,
    requester_id: str,
    target: str,
    destination: str,
) -> dict[str, Any]:
    """Construct the ``task.created.v1`` event for one fanned-out link.

    Each fan-out is its own task with its own ``task_id`` /
    ``idempotency_key`` so retries and finalization behave
    exactly like a regular parse_link. The forum task is
    just the dispatcher; the parse_link tasks are the
    first-class work units.
    """
    task_id = str(uuid.uuid4())
    timestamp = _utc_now_iso()
    return {
        "spec_version": "task.created.v1",
        "task_id": task_id,
        "task_type": "parse_link",
        "idempotency_key": f"forum:{forum_task_id}:{task_id}",
        "created_at": timestamp,
        "payload": {
            "url": url,
            "requester_id": requester_id,
            "target": target,
            "destination": destination,
        },
    }


def build_forum_dispatcher(
    fanout_cap: int = 200,
) -> "ForumDispatcher":
    """Construct a dispatcher using env-driven cookie resolver.

    The cookie resolver is created here (not per-task) so the
    env lookup happens once per worker. Reading env on every
    request is what the cookie module itself does; building
    the resolver is cheap.
    """
    resolver = build_cookie_header_resolver(DEFAULT_HOST_TO_FORUM_KEY)
    client = ForumClient(cookie_resolver=resolver)
    return ForumDispatcher(client=client, fanout_cap=fanout_cap)


class ForumDispatcher:
    """Top-level orchestration: event in, fan-out events out.

    The dispatcher is stateless across tasks — every call to
    :meth:`dispatch` reuses the underlying ``ForumClient`` and
    its cumulative page counter. That counter is the only
    piece of state worth caring about, and it exists so the
    50-page circuit cooldown spans forum tasks (per the
    rationale in :class:`forum.client.ForumClient`).
    """

    def __init__(self, client: ForumClient, fanout_cap: int) -> None:
        self._client = client
        self._fanout_cap = max(1, int(fanout_cap))

    def dispatch(self, event: dict[str, Any]) -> ForumDispatchResult:
        """Run one ``parse_forum_thread`` event end-to-end.

        Returns a :class:`ForumDispatchResult` whose
        ``status`` reflects the outcome:

        * ``SUCCEEDED_WITH_NO_OUTPUT`` — extracted zero
          parser-supported links; the worker should
          publish a task.completed notification so the
          operator sees a friendly "no links" message.
        * ``SUCCEEDED`` — extracted and fanned out
          (possibly truncated by the cap) one or more links.
        * ``FAILED`` — fetch or parse blew up. Per Q2
          (no retry), the caller marks the forum task
          terminal ``FAILED`` immediately.
        """
        forum_task_id = str(event.get("task_id") or "")
        payload = event.get("payload") or {}
        url = str(payload.get("url") or "").strip()
        requester_id = str(payload.get("requester_id") or "").strip()
        target = str(payload.get("target") or "RCLONE").strip()
        destination = str(payload.get("destination") or "115:/").strip()
        max_pages = _coerce_positive_int(
            payload.get("max_pages"),
            default=_coerce_positive_int(
                os.environ.get("FORUM_MAX_PAGES", "10"), 10
            ),
        )

        base_url = _normalize_thread_url(url)
        if not base_url or not requester_id:
            return ForumDispatchResult(
                forum_task_id=forum_task_id,
                status="FAILED",
                message="",
                last_error="invalid forum task payload: url and requester_id are required",
            )

        try:
            extractor_cls = select_extractor_class(base_url)
        except UnsupportedForumError as exc:
            return ForumDispatchResult(
                forum_task_id=forum_task_id,
                status="FAILED",
                message="",
                last_error=str(exc),
            )

        extractor = extractor_cls(self._client)
        thread = extractor.walk(thread_base_url=base_url, max_pages=max_pages)

        if not thread.post_links:
            return ForumDispatchResult(
                forum_task_id=forum_task_id,
                status="SUCCEEDED_WITH_NO_OUTPUT",
                message=(
                    f"no parser-supported links found in last "
                    f"{thread.pages_walked} page(s) of thread"
                ),
                last_page=thread.last_page,
                pages_walked=thread.pages_walked,
                extracted_links=0,
                fanned_out_count=0,
            )

        truncated = 0
        kept = thread.post_links
        if len(kept) > self._fanout_cap:
            truncated = len(kept) - self._fanout_cap
            kept = kept[: self._fanout_cap]

        fanned: list[dict[str, Any]] = []
        for link in kept:
            fanned.append(
                _build_fanout_event(
                    forum_task_id=forum_task_id,
                    url=link,
                    requester_id=requester_id,
                    target=target,
                    destination=destination,
                )
            )

        suffix = ""
        if truncated:
            suffix = f" (truncated {truncated})"
        message = f"fanned out {len(fanned)} parse_link task(s) from {thread.pages_walked} page(s){suffix}"
        return ForumDispatchResult(
            forum_task_id=forum_task_id,
            status="SUCCEEDED",
            message=message,
            last_page=thread.last_page,
            pages_walked=thread.pages_walked,
            extracted_links=len(thread.post_links),
            fanned_out_count=len(fanned),
            fanned_out_events=fanned,
        )


__all__ = [
    "DEFAULT_FORUM_THREAD_QUEUE_KEY",
    "ForumDispatchResult",
    "ForumDispatcher",
    "UnsupportedForumError",
    "build_forum_dispatcher",
]
