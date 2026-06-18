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

    # The retry-button tests below pin the contract the
    # tg bot's inline ``🔁 重试`` button depends on:
    # single-task re-queue via ``task_id``, requester
    # match enforcement, idempotency on a non-FAILED task,
    # forum task routing, and the round-trip of
    # ``task_type`` so the bot can show the right
    # follow-up copy.
    def test_retry_task_single_id_requeues_failed_task(self):
        request = CreateTaskRequest(
            url="https://example.com/btn.mp4",
            requester_id="u-btn",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)
        self.container.repository.update_status(created.task_id, "FAILED", "boom")
        before = len(self.container.publisher.items)

        out = self.container.service.admin_retry_action(
            mode="failed",
            task_id=created.task_id,
            requester_id="u-btn",
        )
        self.assertTrue(out["accepted"])
        self.assertEqual(out["retried"], 1)
        self.assertIn(created.task_id, out["task_ids"])
        self.assertEqual(out["task_type"], "parse_link")
        # ``_republish`` cleared ``last_error`` and rewrote
        # ``status`` to QUEUED, so the next run starts
        # clean.
        task = self.container.service.get_task(created.task_id)
        self.assertEqual(task.status, "QUEUED")
        self.assertEqual(task.last_error, "")
        # Exactly one new event on the publisher: the
        # original create + the retry. (Admin bulk-retry
        # would also add one, so we count delta.)
        self.assertEqual(len(self.container.publisher.items), before + 1)
        # The replayed event is a parse_link event (not
        # forum), so it lands on the regular queue.
        replayed = self.container.publisher.items[-1]
        self.assertEqual(replayed["task_type"], "parse_link")
        self.assertEqual(replayed["task_id"], created.task_id)

    def test_retry_task_rejects_cross_operator(self):
        # Operator A submits the task; operator B hits
        # the retry button. B must see the same response
        # as if the task did not exist — the reason
        # returned in the dict is ``task_not_found``
        # (NOT ``requester_mismatch``) so the api layer
        # does not leak whether the task is real.
        request = CreateTaskRequest(
            url="https://example.com/private.mp4",
            requester_id="u-owner",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)
        self.container.repository.update_status(created.task_id, "FAILED", "boom")
        before = len(self.container.publisher.items)

        out = self.container.service.admin_retry_action(
            mode="failed",
            task_id=created.task_id,
            requester_id="u-attacker",
        )
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "task_not_found")
        # No event was published — the cross-operator
        # retry is rejected before the publish step.
        self.assertEqual(len(self.container.publisher.items), before)

    def test_retry_task_admin_can_retry_any_task(self):
        # The dashboard's bulk-retry path passes
        # ``is_admin=True`` so the requester check is
        # bypassed. Verify an admin retry on someone
        # else's task still works.
        request = CreateTaskRequest(
            url="https://example.com/admin.mp4",
            requester_id="u-owner",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)
        self.container.repository.update_status(created.task_id, "FAILED", "boom")

        out = self.container.service.admin_retry_action(
            mode="failed",
            task_id=created.task_id,
            requester_id="u-admin",
            is_admin=True,
        )
        self.assertTrue(out["accepted"])
        self.assertIn(created.task_id, out["task_ids"])

    def test_retry_task_idempotent_on_non_failed(self):
        # Two operators hit the button at the same
        # instant: the first one re-queues the task
        # (FAILED -> QUEUED), the second one must see
        # ``task_not_failed`` and not republish.
        request = CreateTaskRequest(
            url="https://example.com/race.mp4",
            requester_id="u-1",
            target="RCLONE",
            destination="/incoming",
        )
        created = self.container.service.create_parse_task(request)
        self.container.repository.update_status(created.task_id, "FAILED", "boom")

        first = self.container.service.admin_retry_action(
            mode="failed", task_id=created.task_id, requester_id="u-1"
        )
        self.assertTrue(first["accepted"])
        before = len(self.container.publisher.items)
        second = self.container.service.admin_retry_action(
            mode="failed", task_id=created.task_id, requester_id="u-1"
        )
        self.assertFalse(second["accepted"])
        self.assertEqual(second["reason"], "task_not_failed")
        self.assertEqual(len(self.container.publisher.items), before)

    def test_retry_forum_task_publishes_to_forum_queue(self):
        # Forum tasks must round-trip through the forum
        # publisher (not the parse-link one) so they
        # land on the correct celery queue.
        from app.models import CreateForumTaskRequest

        request = CreateForumTaskRequest(
            url="https://forum.example.com/thread/1",
            requester_id="u-forum",
            target="RCLONE",
            destination="/incoming",
            max_pages=5,
        )
        created = self.container.service.create_forum_thread_task(request)
        self.assertEqual(created.task_type, "parse_forum_thread")
        self.container.repository.update_status(created.task_id, "FAILED", "boom")

        out = self.container.service.admin_retry_action(
            mode="failed", task_id=created.task_id, requester_id="u-forum"
        )
        self.assertTrue(out["accepted"])
        self.assertEqual(out["task_type"], "parse_forum_thread")
        # The replayed event must be on the forum
        # publisher, not the parse-link one — that's
        # the whole reason we round-trip ``task_type``.
        self.assertEqual(len(self.container.publisher.forum_items), 2)
        self.assertEqual(self.container.publisher.forum_items[-1]["task_id"], created.task_id)
        self.assertEqual(self.container.publisher.forum_items[-1]["task_type"], "parse_forum_thread")

    def test_retry_unknown_task_id_returns_not_found(self):
        out = self.container.service.admin_retry_action(
            mode="failed", task_id="does-not-exist", requester_id="u-1"
        )
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "task_not_found")


if __name__ == "__main__":
    unittest.main()
