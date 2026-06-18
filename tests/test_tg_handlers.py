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
        return {
            "parse": 0,
            "download": 0,
            "download_sources": 0,
            "upload": 0,
            "upload_sources": 0,
        }

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

    def cleanup_downloads(self, **kwargs):
        self.calls.append(("cleanup_downloads", kwargs))
        return {
            "accepted": True,
            "root": "/tmp/media-shuttle",
            "dry_run": kwargs.get("dry_run", False),
            "scanned": 1,
            "removed": 1 if not kwargs.get("dry_run") else 0,
            "skipped": 0,
            "freed_bytes": 12345 if not kwargs.get("dry_run") else 0,
            "items": [],
        }


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
        handlers.on_cleanup_command(dry_run=False)
        handlers.on_cleanup_command(dry_run=True)

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
                "cleanup_downloads",
                "cleanup_downloads",
            ],
        )

    def test_cleanup_dry_vs_real(self):
        from tg.handlers import format_cleanup_reply

        api = FakeApiClient()
        handlers = TgHandlers(api)

        real = handlers.on_cleanup_command(dry_run=False)
        self.assertEqual(api.calls[-1], ("cleanup_downloads", {"dry_run": False}))
        self.assertIn("CLEANUP", format_cleanup_reply(real))
        self.assertNotIn("DRY-RUN", format_cleanup_reply(real))

        preview = handlers.on_cleanup_command(dry_run=True)
        self.assertEqual(api.calls[-1], ("cleanup_downloads", {"dry_run": True}))
        rendered = format_cleanup_reply(preview)
        self.assertIn("DRY-RUN", rendered)
        self.assertIn("freed=0 B", rendered)

    def test_cleanup_failure_renders(self):
        from tg.handlers import format_cleanup_reply

        self.assertIn("cleanup failed", format_cleanup_reply(None))
        self.assertIn(
            "sweep_unavailable",
            format_cleanup_reply({"accepted": False, "reason": "sweep_unavailable"}),
        )

    def test_monitor_renders_task_and_source_counts(self):
        # A 247-file album in flight should show as
        # ``download sources: 247`` (the useful view for
        # operators) while still showing ``download tasks: 1``
        # so a single-file link is sanity-checkable.
        from tg.handlers import TgHandlers

        class _Api:
            def queue_stats(self):
                return {
                    "parse": 0,
                    "download": 1,
                    "download_sources": 247,
                    "upload": 0,
                    "upload_sources": 0,
                }

        out = TgHandlers(_Api()).on_monitor_command()
        self.assertIn("download tasks", out)
        self.assertIn("download sources", out)
        self.assertIn("upload tasks", out)
        self.assertIn("upload sources", out)
        # The source-level value must appear next to its label
        # so the operator reads the right number, not the task
        # count by accident.
        self.assertIn("247", out)
        self.assertIn("📊 monitor", out)

    def test_monitor_tolerates_legacy_stats_payload(self):
        # An older api (or a hand-rolled mock) might still
        # return the pre-source-counts shape. The handler
        # must not crash; it should default the source
        # counts to 0.
        from tg.handlers import TgHandlers

        class _Api:
            def queue_stats(self):
                return {"parse": 0, "download": 1, "upload": 0}

        out = TgHandlers(_Api()).on_monitor_command()
        self.assertIn("download tasks", out)
        self.assertIn("download sources", out)
        # Source counts default to 0 even when the api
        # didn't return them.
        self.assertIn("0", out)


if __name__ == "__main__":
    unittest.main()
