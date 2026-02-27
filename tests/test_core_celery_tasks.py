import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.bootstrap import build_core_service
from core.enums import UploadTarget
from core.queue.tasks import _download_queue_for_site, process_created_event_logic


class _FakeCeleryApp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_task(self, name, args=None, queue=None, routing_key=None, serializer=None):
        self.calls.append(
            {
                "name": name,
                "args": args or [],
                "queue": queue,
                "routing_key": routing_key,
                "serializer": serializer,
            }
        )


class TestCoreCeleryTasks(unittest.TestCase):
    def test_download_queue_should_include_site_suffix(self):
        self.assertEqual(_download_queue_for_site("GOFILE"), "media_shuttle:task_download@GOFILE")

    def test_process_created_event_should_retry_when_task_failed(self):
        service = build_core_service()
        service.pipeline.uploader_registry.register(
            lambda target: target == UploadTarget.RCLONE.value,
            lambda _download, _destination: (_ for _ in ()).throw(ValueError("forced upload failure")),
        )
        fake_app = _FakeCeleryApp()
        event = {
            "spec_version": "task.created.v1",
            "task_id": "task-retry-1",
            "task_type": "parse_link",
            "idempotency_key": "idem-retry-1",
            "created_at": "2026-02-27T12:00:00Z",
            "payload": {
                "url": "https://example.com/retry.mp4",
                "requester_id": "u-retry",
                "target": "RCLONE",
                "destination": "/dest",
            },
        }

        old_retry = os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY")
        old_max_retries = os.getenv("MEDIA_SHUTTLE_MAX_RETRIES")
        os.environ["MEDIA_SHUTTLE_RETRY_QUEUE_KEY"] = "media_shuttle:task_retry"
        os.environ["MEDIA_SHUTTLE_MAX_RETRIES"] = "2"
        try:
            out = process_created_event_logic(event=event, app=fake_app, service=service)
        finally:
            if old_retry is None:
                os.environ.pop("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", None)
            else:
                os.environ["MEDIA_SHUTTLE_RETRY_QUEUE_KEY"] = old_retry
            if old_max_retries is None:
                os.environ.pop("MEDIA_SHUTTLE_MAX_RETRIES", None)
            else:
                os.environ["MEDIA_SHUTTLE_MAX_RETRIES"] = old_max_retries

        self.assertEqual(out["state"], "retried")
        self.assertEqual(len(fake_app.calls), 1)
        self.assertEqual(fake_app.calls[0]["queue"], "media_shuttle:task_retry")

    def test_process_created_event_should_succeed_without_retry(self):
        service = build_core_service()
        fake_app = _FakeCeleryApp()
        event = {
            "spec_version": "task.created.v1",
            "task_id": "task-ok-1",
            "task_type": "parse_link",
            "idempotency_key": "idem-ok-1",
            "created_at": "2026-02-27T12:00:00Z",
            "payload": {
                "url": "https://example.com/ok.mp4",
                "requester_id": "u-ok",
                "target": "RCLONE",
                "destination": "/dest",
            },
        }

        out = process_created_event_logic(event=event, app=fake_app, service=service)
        self.assertEqual(out["state"], "succeeded")
        self.assertEqual(len(fake_app.calls), 0)


if __name__ == "__main__":
    unittest.main()
