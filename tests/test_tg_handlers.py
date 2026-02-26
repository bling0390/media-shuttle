import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-tg").resolve()))

from tg.handlers import TgHandlers


class FakeApiClient:
    def __init__(self):
        self.calls = []

    def create_parse_task(self, **kwargs):
        self.calls.append(("create_parse_task", kwargs))
        return {"task_id": "t-1", "status": "QUEUED"}

    def queue_stats(self):
        self.calls.append(("queue_stats", {}))
        return {"parse": 0, "download": 0, "upload": 0}

    def admin_worker(self, **kwargs):
        self.calls.append(("admin_worker", kwargs))
        return {"accepted": True}

    def admin_rate_limit(self, **kwargs):
        self.calls.append(("admin_rate_limit", kwargs))
        return {"accepted": True}

    def admin_retry(self, **kwargs):
        self.calls.append(("admin_retry", kwargs))
        return {"accepted": True}

    def admin_setting(self, **kwargs):
        self.calls.append(("admin_setting", kwargs))
        return {"accepted": True}


class TestTgHandlers(unittest.TestCase):
    def test_handlers_use_api_only(self):
        api = FakeApiClient()
        handlers = TgHandlers(api)

        resp = handlers.on_leech_command("u1", "https://example.com", "RCLONE", "/")
        self.assertEqual(resp["status"], "QUEUED")
        handlers.on_monitor_command()
        handlers.on_worker_command("w1", "q1", 2)
        handlers.on_rate_command("w1", "download", "1/s")
        handlers.on_retry_command("both")
        handlers.on_setting_command("upload.tool", "RCLONE")

        call_names = [name for name, _ in api.calls]
        self.assertEqual(
            call_names,
            [
                "create_parse_task",
                "queue_stats",
                "admin_worker",
                "admin_rate_limit",
                "admin_retry",
                "admin_setting",
            ],
        )


if __name__ == "__main__":
    unittest.main()
