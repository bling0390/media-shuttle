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

    def test_create_task_should_accept_telegram_destination(self):
        request = CreateTaskRequest(
            url="https://example.com/file.mp4",
            requester_id="u-telegram",
            target="TELEGRAM",
            destination="tg://chat/@media_shuttle",
        )
        record = self.container.service.create_parse_task(request)
        self.assertEqual(record.target, "TELEGRAM")
        self.assertEqual(record.destination, "tg://chat/@media_shuttle")

    def test_create_task_should_reject_invalid_telegram_destination(self):
        request = CreateTaskRequest(
            url="https://example.com/file.mp4",
            requester_id="u-telegram",
            target="TELEGRAM",
            destination="channel:@media_shuttle",
        )
        with self.assertRaises(ValueError):
            self.container.service.create_parse_task(request)

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

    def test_admin_worker_should_persist_worker_state(self):
        out = self.container.service.admin_worker_action(worker="w1", queue="q1", concurrency=2)
        self.assertTrue(out["accepted"])

        workers = self.container.service.list_workers(refresh=False)
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0].hostname, "w1")
        self.assertEqual(workers[0].concurrency, 2)
        self.assertIn("q1", workers[0].queues)
        self.assertEqual(workers[0].status, "READY")

    def test_admin_worker_shutdown_should_update_status(self):
        self.container.service.admin_worker_action(worker="w2", queue="", concurrency=1)
        out = self.container.service.admin_worker_action(worker="w2", queue="", concurrency=0)
        self.assertTrue(out["accepted"])
        self.assertEqual(out["action"], "shutdown")

        worker = self.container.worker_repository.get("w2")
        self.assertIsNotNone(worker)
        self.assertEqual(worker.status, "SHUTDOWN")

    def test_admin_rate_limit_should_persist_worker_rate_limit(self):
        out = self.container.service.admin_rate_limit_action(worker="w3", task_type="upload", rate_limit="5/m")
        self.assertTrue(out["accepted"])
        self.assertEqual(out["task_name"], "core.queue.tasks.process_upload_result")

        worker = self.container.worker_repository.get("w3")
        self.assertIsNotNone(worker)
        self.assertEqual(worker.rate_limits["core.queue.tasks.process_upload_result"], "5/m")

    def test_list_workers_should_merge_live_inspect_result(self):
        class _FakeControl:
            def inspect_workers(self):
                return {
                    "core-worker-parse@media-shuttle-core": {
                        "hostname": "core-worker-parse@media-shuttle-core",
                        "status": "READY",
                        "concurrency": 3,
                        "queues": ["media_shuttle:task_created", "media_shuttle:task_retry"],
                        "queue": "media_shuttle:task_created,media_shuttle:task_retry",
                    }
                }

            def add_queue(self, worker: str, queue: str):
                return {"accepted": True}

            def set_concurrency(self, worker: str, concurrency: int):
                return {"accepted": True, "after": concurrency}

            def shutdown(self, worker: str):
                return {"accepted": True}

            def set_rate_limit(self, worker: str, task_name: str, rate_limit: str):
                return {"accepted": True}

            def publish_control_command(self, **kwargs):
                return {"accepted": True, "queue": "media_shuttle:worker_control@NODE-A", "command": kwargs}

        container = build_container(worker_control=_FakeControl())
        items = container.service.list_workers(refresh=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].hostname, "core-worker-parse@media-shuttle-core")
        self.assertEqual(items[0].concurrency, 3)

    def test_admin_worker_start_should_publish_control_command(self):
        class _FakeControl:
            def __init__(self) -> None:
                self.calls = []

            def inspect_workers(self):
                return {}

            def add_queue(self, worker: str, queue: str):
                return {"accepted": True}

            def set_concurrency(self, worker: str, concurrency: int):
                return {"accepted": True, "after": concurrency}

            def shutdown(self, worker: str):
                return {"accepted": True}

            def set_rate_limit(self, worker: str, task_name: str, rate_limit: str):
                return {"accepted": True}

            def publish_control_command(self, **kwargs):
                self.calls.append(kwargs)
                return {"accepted": True, "queue": "media_shuttle:worker_control@NODE_A", "command": kwargs}

        control = _FakeControl()
        container = build_container(worker_control=control)
        out = container.service.admin_worker_action(
            worker="",
            queue="",
            concurrency=3,
            action="start",
            node_id="node-a",
            role="download",
        )
        self.assertTrue(out["accepted"])
        self.assertEqual(out["action"], "start")
        self.assertEqual(out["role"], "download")
        self.assertEqual(len(control.calls), 1)
        self.assertEqual(control.calls[0]["node_id"], "node-a")
        self.assertEqual(control.calls[0]["role"], "download")
        self.assertEqual(control.calls[0]["concurrency"], 3)


if __name__ == "__main__":
    unittest.main()
