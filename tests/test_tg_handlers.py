import sys
import unittest
from pathlib import Path

import httpx

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

    def retry_task(self, **kwargs):
        # The default fake returns a successful retry
        # so the bulk of the existing handler tests keep
        # passing. The retry-specific tests below patch
        # this method to return failures / raise.
        self.calls.append(("retry_task", kwargs))
        return {
            "accepted": True,
            "task_id": kwargs.get("task_id", ""),
            "task_type": "parse_link",
        }

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


class TestRetryTaskHandler(unittest.TestCase):
    """Cover ``on_retry_task_command`` (the inline 🔁 重试 button).

    The handler is the seam between the bot's
    CallbackQueryHandler and the api's
    ``POST /v1/tasks/<task_id>/retry`` endpoint. Tests
    focus on translating api responses into the small
    dict the callback handler consumes, including the
    cross-operator 404 / already-retried 409 branches
    that the bot surfaces as toasts.
    """

    def test_missing_task_id_short_circuits(self):
        api = FakeApiClient()
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_task_id")
        # ``retry_task`` must NOT be called when the
        # task_id is empty — otherwise an empty string
        # would be POSTed to ``/v1/tasks//retry`` and
        # 404 from the api.
        self.assertNotIn("retry_task", [name for name, _ in api.calls])

    def test_missing_requester_short_circuits(self):
        api = FakeApiClient()
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_requester")
        self.assertNotIn("retry_task", [name for name, _ in api.calls])

    def test_successful_parse_link_retry(self):
        # Forum tasks have their own follow-up copy; a
        # single-link retry just shows "🔁 重试中…".
        api = FakeApiClient()
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "accepted")
        self.assertEqual(result["task_id"], "t-1")
        self.assertEqual(result["task_type"], "parse_link")
        self.assertIn("重试", result["message"])
        # Confirm the api was hit with the requester_id
        # the bot has in scope — not a server-derived
        # one.
        retry_calls = [c for c in api.calls if c[0] == "retry_task"]
        self.assertEqual(len(retry_calls), 1)
        self.assertEqual(retry_calls[0][1]["requester_id"], "u-1")
        self.assertEqual(retry_calls[0][1]["task_id"], "t-1")

    def test_successful_forum_retry_shows_specific_copy(self):
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            return {
                "accepted": True,
                "task_id": kwargs.get("task_id", ""),
                "task_type": "parse_forum_thread",
            }

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-9", requester_id="u-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["task_type"], "parse_forum_thread")
        self.assertIn("抓取", result["message"])

    def test_404_translates_to_not_found(self):
        # The api returns 404 for both a real missing
        # task AND a cross-operator mismatch. The
        # handler must surface them the same way so the
        # bot never leaks whether someone else's task
        # exists.
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            request = httpx.Request("POST", "http://test/v1/tasks/t-1/retry")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                "not found", request=request, response=response
            )

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "not_found")
        self.assertIn("无权", result["message"])

    def test_409_translates_to_already_retried(self):
        # Two operators hitting the button at the same
        # instant — the second one should see a clear
        # "already retried" toast, not a generic 404.
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            request = httpx.Request("POST", "http://test/v1/tasks/t-1/retry")
            response = httpx.Response(409, request=request)
            raise httpx.HTTPStatusError(
                "conflict", request=request, response=response
            )

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "already_retried")
        self.assertIn("已重试", result["message"])

    def test_403_translates_to_missing_requester(self):
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            request = httpx.Request("POST", "http://test/v1/tasks/t-1/retry")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError(
                "forbidden", request=request, response=response
            )

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_requester")

    def test_other_http_status_translates_to_http_error(self):
        # 500s and the like should bubble up as an
        # http_error so the operator knows it's not
        # their fault.
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            request = httpx.Request("POST", "http://test/v1/tasks/t-1/retry")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError(
                "server error", request=request, response=response
            )

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "http_error")
        self.assertIn("500", result["message"])

    def test_unexpected_transport_error_is_caught(self):
        # Connection errors, dns failures, etc. must
        # not escape — the bot lives in an event loop
        # and an uncaught exception would just log
        # "Update is handled" without telling the
        # operator anything.
        api = FakeApiClient()

        def fake_retry_task(**kwargs):
            api.calls.append(("retry_task", kwargs))
            raise RuntimeError("dns failure")

        api.retry_task = fake_retry_task
        handlers = TgHandlers(api)
        result = handlers.on_retry_task_command(task_id="t-1", requester_id="u-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "transport_error")
        self.assertIn("dns failure", result["message"])


if __name__ == "__main__":
    unittest.main()
