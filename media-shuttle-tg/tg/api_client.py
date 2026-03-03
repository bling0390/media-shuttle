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

    def create_parse_task(self, url: str, requester_id: str, target: str, destination: str) -> dict:
        return self._request(
            "POST",
            "/v1/tasks/parse",
            body={
                "url": url,
                "requester_id": requester_id,
                "target": target,
                "destination": destination,
            },
        )

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
