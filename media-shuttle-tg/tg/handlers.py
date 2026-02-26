from __future__ import annotations

from dataclasses import dataclass

from .api_client import ApiClient


@dataclass
class LeechSession:
    requester_id: str
    target: str = "RCLONE"
    destination: str = "/"


class TgHandlers:
    def __init__(self, api_client: ApiClient) -> None:
        self.api = api_client

    def on_leech_command(self, requester_id: str, url: str, target: str, destination: str) -> dict:
        return self.api.create_parse_task(
            url=url,
            requester_id=requester_id,
            target=target,
            destination=destination,
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
