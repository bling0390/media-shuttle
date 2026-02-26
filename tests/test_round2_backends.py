import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))
sys.path.insert(0, str(Path("media-shuttle-core").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from app.container import build_container
from app.queue import RedisTaskPublisher
from app.repository import MongoTaskRepository
from core.bootstrap import build_core_service
from core.storage.repository import MongoTaskRepository as CoreMongoTaskRepository
from fakes import FakeMongoClient, FakeRedis


class TestRound2Backends(unittest.TestCase):
    def test_api_container_can_use_mongo_and_redis(self):
        fake_mongo = FakeMongoClient()
        fake_redis = FakeRedis()
        container = build_container(
            repository_backend="mongo",
            queue_backend="redis",
            mongo_client=fake_mongo,
            redis_client=fake_redis,
        )
        self.assertIsInstance(container.repository, MongoTaskRepository)
        self.assertIsInstance(container.publisher, RedisTaskPublisher)

    def test_core_can_use_mongo_repository(self):
        fake_mongo = FakeMongoClient()
        service = build_core_service(repository_backend="mongo", mongo_client=fake_mongo)
        self.assertIsInstance(service.repository, CoreMongoTaskRepository)


if __name__ == "__main__":
    unittest.main()
