import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))

from app.container import build_container
from app.models import CreateTaskRequest


class TestApiService(unittest.TestCase):
    def setUp(self):
        self.container = build_container()

    def test_create_task_should_publish_event(self):
        request = CreateTaskRequest(
            url="https://example.com/file.mp4",
            requester_id="u-100",
            target="RCLONE",
            destination="/incoming",
        )
        record = self.container.service.create_parse_task(request)
        self.assertEqual(record.status, "QUEUED")
        self.assertEqual(len(self.container.publisher.items), 1)
        event = self.container.publisher.items[0]
        self.assertEqual(event["spec_version"], "task.created.v1")
        self.assertEqual(event["payload"]["url"], request.url)

    def test_list_and_get_task(self):
        request = CreateTaskRequest(
            url="https://example.com/a.mp4",
            requester_id="u-1",
            target="RCLONE",
            destination="media",
        )
        created = self.container.service.create_parse_task(request)
        found = self.container.service.get_task(created.task_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, created.task_id)
        items = self.container.service.list_tasks(status="QUEUED", limit=10)
        self.assertGreaterEqual(len(items), 1)

    def test_admin_retry_should_requeue_failed_tasks(self):
        request = CreateTaskRequest(
            url="https://example.com/retry.mp4",
            requester_id="u-retry",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)
        self.container.repository.update_status(created.task_id, "FAILED", "forced")

        out = self.container.service.admin_retry_action(mode="failed")
        self.assertTrue(out["accepted"])
        self.assertEqual(out["retried"], 1)
        self.assertIn(created.task_id, out["task_ids"])

        task = self.container.service.get_task(created.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "QUEUED")
        # first publish on create, second publish on manual retry
        self.assertEqual(len(self.container.publisher.items), 2)

    def test_admin_retry_should_reject_non_failed_task(self):
        request = CreateTaskRequest(
            url="https://example.com/no-retry.mp4",
            requester_id="u-no-retry",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)

        out = self.container.service.admin_retry_action(mode="failed", task_id=created.task_id)
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "task_not_failed")


if __name__ == "__main__":
    unittest.main()
