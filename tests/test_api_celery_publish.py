import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from app.queue import RedisTaskPublisher
from app.worker_control import CeleryWorkerControl
from fakes import FakeRedis


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


class TestApiCeleryPublish(unittest.TestCase):
    def test_redis_publisher_should_send_celery_task_when_enabled(self):
        fake_celery = _FakeCeleryApp()
        publisher = RedisTaskPublisher(
            redis_url="redis://localhost:6379/0",
            queue_key="media_shuttle:task_created",
            use_celery=True,
            celery_app=fake_celery,
            celery_task_name="core.queue.tasks.process_created_event",
        )

        event = {"task_id": "t1", "payload": {"url": "https://example.com/file.mp4"}}
        publisher.publish_created_event(event)

        self.assertEqual(len(fake_celery.calls), 1)
        self.assertEqual(fake_celery.calls[0]["name"], "core.queue.tasks.process_created_event")
        self.assertEqual(fake_celery.calls[0]["args"], [event])
        self.assertEqual(fake_celery.calls[0]["queue"], "media_shuttle:task_created")

    def test_redis_publisher_should_use_redis_list_with_explicit_client(self):
        fake_redis = FakeRedis()
        publisher = RedisTaskPublisher(
            redis_url="redis://localhost:6379/0",
            queue_key="media_shuttle:task_created",
            client=fake_redis,
        )

        event = {"task_id": "t2"}
        publisher.publish_created_event(event)
        got = publisher.pop_created_event(timeout_seconds=0)
        self.assertEqual(got["task_id"], "t2")

    def test_worker_control_should_publish_node_targeted_control_task(self):
        fake_celery = _FakeCeleryApp()
        control = CeleryWorkerControl(
            redis_url="redis://localhost:6379/0",
            celery_app=fake_celery,
            control_queue_prefix="media_shuttle:worker_control",
            control_task_name="core.queue.tasks.apply_worker_control",
        )

        out = control.publish_control_command(
            node_id="node-a",
            role="parse",
            action="start",
            concurrency=2,
        )

        self.assertTrue(out["accepted"])
        self.assertEqual(len(fake_celery.calls), 1)
        self.assertEqual(fake_celery.calls[0]["name"], "core.queue.tasks.apply_worker_control")
        self.assertEqual(fake_celery.calls[0]["queue"], "media_shuttle:worker_control@NODE-A")
        payload = fake_celery.calls[0]["args"][0]
        self.assertEqual(payload["role"], "parse")
        self.assertEqual(payload["action"], "start")


if __name__ == "__main__":
    unittest.main()
