import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))
sys.path.insert(0, str(Path("media-shuttle-core").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from app.container import build_container
from app.models import CreateTaskRequest
from core.bootstrap import build_core_service
from core.runtime import CoreRuntime, RuntimeConfig
from fakes import FakeMongoClient, FakeRedis


class TestCoreRuntimeConcurrency(unittest.TestCase):
    def test_runtime_should_process_tasks_with_multiple_workers(self):
        fake_mongo = FakeMongoClient()
        fake_redis = FakeRedis()

        api = build_container(
            repository_backend="mongo",
            queue_backend="redis",
            mongo_client=fake_mongo,
            redis_client=fake_redis,
        )

        created_ids = []
        for idx in range(6):
            created = api.service.create_parse_task(
                CreateTaskRequest(
                    url=f"https://example.com/{idx}.mp4",
                    requester_id="u-concurrency",
                    target="RCLONE",
                    destination="/dest",
                )
            )
            created_ids.append(created.task_id)

        core_service = build_core_service(repository_backend="mongo", mongo_client=fake_mongo)
        runtime = CoreRuntime(
            service=core_service,
            config=RuntimeConfig(
                queue_backend="redis",
                max_retries=1,
                created_queue_key="media_shuttle:task_created",
                retry_queue_key="media_shuttle:task_retry",
                concurrency=3,
                poll_seconds=0,
            ),
            redis_client=fake_redis,
        )

        processed = runtime.run_workers_once(steps_per_worker=2, timeout_seconds=0)
        self.assertEqual(processed, 6)

        for task_id in created_ids:
            task = api.service.get_task(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
