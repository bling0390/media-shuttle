from __future__ import annotations

import httpx

from .config import API_BASE_URL


class ApiClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or API_BASE_URL).rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        response = httpx.request(
            method=method,
            url=f"{self.base_url}{path}",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=20.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.text
        return response.json() if payload else {}

    def create_parse_task(
        self, url: str, requester_id: str, target: str, destination: str | None = None
    ) -> dict:
        # ``destination`` is optional for RCLONE targets; the api applies
        # the default ``115:/`` so the final path is
        # ``115:/<date>/<folder>/<file>``.
        body: dict = {
            "url": url,
            "requester_id": requester_id,
            "target": target,
        }
        if destination:
            body["destination"] = destination
        return self._request("POST", "/v1/tasks/parse", body=body)

    def create_forum_task(
        self,
        url: str,
        requester_id: str,
        target: str = "RCLONE",
        destination: str | None = None,
        max_pages: int | None = None,
    ) -> dict:
        """Submit a forum thread for extraction.

        ``max_pages`` is optional: 0 / None means "use the
        server-side default (``FORUM_MAX_PAGES``)". The
        server enforces the upper bound, so the bot never
        needs to clamp.
        """
        body: dict = {
            "url": url,
            "requester_id": requester_id,
            "target": target,
        }
        if destination:
            body["destination"] = destination
        if max_pages:
            body["max_pages"] = int(max_pages)
        return self._request("POST", "/v1/tasks/parse_forum", body=body)

    def queue_stats(self) -> dict:
        return self._request("GET", "/v1/stats/queue")

    def admin_worker(self, worker: str, queue: str, concurrency: int) -> dict:
        return self._request(
            "POST",
            "/v1/admin/workers",
            body={"worker": worker, "queue": queue, "concurrency": concurrency},
        )

    def admin_rate_limit(self, worker: str, task_type: str, rate_limit: str) -> dict:
        return self._request(
            "POST",
            "/v1/admin/rate-limit",
            body={"worker": worker, "task_type": task_type, "rate_limit": rate_limit},
        )

    def admin_retry(self, mode: str) -> dict:
        return self._request("POST", "/v1/admin/retry", body={"mode": mode})

    def admin_setting(self, key: str, value: str) -> dict:
        return self._request("POST", "/v1/admin/settings", body={"key": key, "value": value})

    def cleanup_downloads(self, dry_run: bool = False) -> dict:
        """Wipe the local download directory via the api.

        ``dry_run=True`` previews what would be removed (size
        and path of every direct child of
        ``MEDIA_SHUTTLE_DOWNLOAD_DIR``) without deleting
        anything. ``dry_run=False`` actually removes the
        files. The api enforces a per-candidate path-safety
        check (every path must resolve inside the configured
        download root) so this call can never reach outside
        the worker's working area.
        """
        return self._request(
            "POST",
            "/v1/admin/cleanup-downloads",
            body={"dry_run": bool(dry_run)},
        )
