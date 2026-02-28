import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))
sys.path.insert(0, str(Path("media-shuttle-core").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from app.container import build_container
from app.models import CreateTaskRequest
from core.bootstrap import build_core_service
from core.enums import UploadTarget
from core.runtime import CoreRuntime, RuntimeConfig
from fakes import FakeMongoClient, FakeRedis


class TestCoreRetryDlq(unittest.TestCase):
    def test_failed_task_should_retry_then_stop_without_dead_letter(self):
        fake_mongo = FakeMongoClient()
        fake_redis = FakeRedis()

        api = build_container(
            repository_backend="mongo",
            queue_backend="redis",
            mongo_client=fake_mongo,
            redis_client=fake_redis,
        )

        created = api.service.create_parse_task(
            CreateTaskRequest(
                url="https://example.com/will-fail",
                requester_id="u-retry",
                target="RCLONE",
                destination="/dest",
            )
        )

        core_service = build_core_service(repository_backend="mongo", mongo_client=fake_mongo)
        core_service.pipeline.uploader_registry.register(
            lambda target: target == UploadTarget.RCLONE.value,
            lambda _download, _destination: (_ for _ in ()).throw(ValueError("forced upload failure")),
        )
        runtime = CoreRuntime(
            service=core_service,
            config=RuntimeConfig(
                queue_backend="redis",
                max_retries=2,
                created_queue_key="media_shuttle:task_created",
                retry_queue_key="media_shuttle:task_retry",
                concurrency=1,
                poll_seconds=0,
            ),
            redis_client=fake_redis,
        )

        out1 = runtime.process_one(timeout_seconds=0)
        self.assertEqual(out1["state"], "retried")
        self.assertEqual(fake_redis.llen("media_shuttle:task_retry"), 1)

        out2 = runtime.process_one(timeout_seconds=0)
        self.assertEqual(out2["state"], "retried")
        self.assertEqual(fake_redis.llen("media_shuttle:task_retry"), 1)

        out3 = runtime.process_one(timeout_seconds=0)
        self.assertEqual(out3["state"], "failed")
        self.assertEqual(fake_redis.llen("media_shuttle:task_retry"), 0)
        self.assertEqual(fake_redis.llen("media_shuttle:task_dlq"), 0)

        task = api.service.get_task(created.task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "FAILED")
        self.assertTrue(task.last_error)


if __name__ == "__main__":
    unittest.main()
