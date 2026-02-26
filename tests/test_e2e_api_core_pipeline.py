import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))
sys.path.insert(0, str(Path("media-shuttle-core").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from app.container import build_container
from app.models import CreateTaskRequest
from core.bootstrap import build_core_service
from core.queue.consumer import RedisTaskCreatedConsumer
from core.worker import CoreWorker
from fakes import FakeMongoClient, FakeRedis


class TestApiCoreE2E(unittest.TestCase):
    def test_api_enqueue_core_consume_and_update_status(self):
        fake_mongo = FakeMongoClient()
        fake_redis = FakeRedis()

        api_container = build_container(
            repository_backend="mongo",
            queue_backend="redis",
            mongo_client=fake_mongo,
            redis_client=fake_redis,
        )

        request = CreateTaskRequest(
            url="https://example.com/file.mp4",
            requester_id="u-e2e",
            target="RCLONE",
            destination="/dest",
        )
        created = api_container.service.create_parse_task(request)
        self.assertEqual(created.status, "QUEUED")

        core_service = build_core_service(repository_backend="mongo", mongo_client=fake_mongo)
        worker = CoreWorker(service=core_service)
        consumer = RedisTaskCreatedConsumer(client=fake_redis)

        result = worker.consume_once(consumer)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "SUCCEEDED")

        task = api_container.service.get_task(created.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "SUCCEEDED")
        self.assertTrue(task.message.startswith("rclone://"))


if __name__ == "__main__":
    unittest.main()
