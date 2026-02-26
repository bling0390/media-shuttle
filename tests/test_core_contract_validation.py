import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.queue.contracts import ContractError, validate_task_created_event, validate_task_status_event


class TestContractValidation(unittest.TestCase):
    def test_valid_created_event(self):
        event = {
            "spec_version": "task.created.v1",
            "task_id": "1",
            "task_type": "parse_link",
            "idempotency_key": "k",
            "created_at": "2026-02-26T00:00:00Z",
            "payload": {
                "url": "https://example.com",
                "requester_id": "u",
                "target": "RCLONE",
                "destination": "/",
            },
        }
        validate_task_created_event(event)

    def test_invalid_created_event(self):
        with self.assertRaises(ContractError):
            validate_task_created_event({"spec_version": "task.created.v1"})

    def test_valid_status_event(self):
        event = {
            "spec_version": "task.status.v1",
            "task_id": "1",
            "status": "QUEUED",
            "updated_at": "2026-02-26T00:00:00Z",
        }
        validate_task_status_event(event)


if __name__ == "__main__":
    unittest.main()
