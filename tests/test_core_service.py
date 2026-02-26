import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.bootstrap import build_core_service
from core.models import TaskPayload


class TestCoreService(unittest.TestCase):
    def setUp(self):
        self.service = build_core_service()

    def test_create_and_run_task(self):
        event = {
            "spec_version": "task.created.v1",
            "task_id": "task-1",
            "task_type": "parse_link",
            "idempotency_key": "idem-1",
            "created_at": "2026-02-26T00:00:00Z",
            "payload": {
                "url": "https://example.com/video.mp4",
                "requester_id": "u-1",
                "target": "RCLONE",
                "destination": "/movies",
            },
        }
        record = self.service.create_task_from_event(event)
        self.assertEqual(record.task_id, "task-1")
        result = self.service.run_task("task-1")
        self.assertEqual(result.status.value, "SUCCEEDED")
        self.assertTrue(result.message.startswith("rclone://"))

    def test_task_failed_on_unsupported_target(self):
        event = {
            "spec_version": "task.created.v1",
            "task_id": "task-2",
            "task_type": "parse_link",
            "idempotency_key": "idem-2",
            "created_at": "2026-02-26T00:00:00Z",
            "payload": {
                "url": "https://example.com/video.mp4",
                "requester_id": "u-1",
                "target": "UNKNOWN",
                "destination": "/movies",
            },
        }
        self.service.create_task_from_event(event)
        result = self.service.run_task("task-2")
        self.assertEqual(result.status.value, "FAILED")
        self.assertIn("unsupported target", result.message)

    def test_core_has_no_pyrogram_dependency(self):
        core_root = Path("media-shuttle-core/core")
        for path in core_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pyrogram", text, f"pyrogram dependency found in {path}")


if __name__ == "__main__":
    unittest.main()
