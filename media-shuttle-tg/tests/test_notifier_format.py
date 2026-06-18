"""Unit tests for the tg notifier message format.

These cover the three notification styles shipped by
``tg.notifier`` (``box`` / ``kv`` / ``inline``) and the
new event-payload fields (``source_site`` /
``duration_seconds``) added to make them meaningful.

All tests are offline — no network, no redis, no
pyrogram. We import the notifier module directly and
exercise its pure-Python formatters.
"""

from __future__ import annotations

import os
import unittest

from tg.notifier import (
    _format_bytes,
    _format_box,
    _format_duration,
    _format_kv,
    _format_notification,
    _truncate,
    _build_retry_button,
    _build_retry_markup,
    _parse_ok,
)


def _set_style(style: str | None) -> None:
    if style is None:
        os.environ.pop("TG_NOTIFY_STYLE", None)
    else:
        os.environ["TG_NOTIFY_STYLE"] = style


class FormatBytesTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_format_bytes(0), "0 B")

    def test_bytes(self):
        self.assertEqual(_format_bytes(512), "512 B")

    def test_kib(self):
        self.assertEqual(_format_bytes(1024), "1.0 KiB")

    def test_mib(self):
        self.assertEqual(_format_bytes(1024 * 1024 * 12 + 300 * 1024), "12.3 MiB")

    def test_gib(self):
        self.assertEqual(
            _format_bytes(4 * 1024 ** 3 + 200 * 1024 ** 2),
            "4.2 GiB",
        )

    def test_tib(self):
        self.assertEqual(_format_bytes(2 * 1024 ** 4), "2.0 TiB")


class FormatDurationTests(unittest.TestCase):
    def test_zero_returns_empty(self):
        self.assertEqual(_format_duration(0), "")

    def test_negative_returns_empty(self):
        self.assertEqual(_format_duration(-5), "")

    def test_seconds_only(self):
        self.assertEqual(_format_duration(45), "45s")

    def test_minutes_and_seconds(self):
        self.assertEqual(_format_duration(75), "1m15s")

    def test_hours_minutes_seconds(self):
        self.assertEqual(_format_duration(3725), "1h2m5s")


class TruncateTests(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(_truncate("abc", 10), "abc")

    def test_exact_length_unchanged(self):
        self.assertEqual(_truncate("abc", 3), "abc")

    def test_long_truncated(self):
        out = _truncate("abcdefghij", 5)
        self.assertEqual(len(out), 5)
        self.assertTrue(out.endswith("…"))

    def test_max_len_one(self):
        self.assertEqual(_truncate("abc", 1), "a")


class BoxFormatTests(unittest.TestCase):
    def test_basic_box_structure(self):
        body = _format_box(
            name="file.mp4",
            size=1024,
            source="bunkrr.su",
            location="rclone://115:/x",
            duration="5s",
        )
        # Strip the surrounding ``<pre>`` / ``</pre>`` tags
        # so the line-by-line checks below see the table
        # rows directly.
        if body.startswith("<pre>"):
            body = body[len("<pre>"):]
        if body.endswith("</pre>"):
            body = body[: -len("</pre>")]
        lines = body.split("\n")
        # 7 lines: status + separator + filename + size + source
        # + location + duration. The outer box border is gone;
        # only the separator after the status line is kept.
        self.assertEqual(len(lines), 7)
        # All rows must share the same length (no leading /
        # trailing ``│`` to strip now, so the comparison is
        # simpler than the old box).
        row_lens = {len(line) for line in lines}
        self.assertEqual(len(row_lens), 1, f"rows have varying lengths: {row_lens}")
        # Status row is on top, separator immediately after.
        self.assertIn("✅ 上传成功", lines[0])
        self.assertTrue(lines[1].startswith("─"))
        self.assertNotIn("┌", body)
        self.assertNotIn("└", body)
        self.assertNotIn("│", body)

    def test_omits_optional_rows(self):
        # No source / location / duration.
        body = _format_box(
            name="x.mp4",
            size=2048,
            source="",
            location="",
            duration="",
        )
        if body.startswith("<pre>"):
            body = body[len("<pre>"):]
        if body.endswith("</pre>"):
            body = body[: -len("</pre>")]
        lines = body.split("\n")
        # 4 lines: status + separator + filename + size.
        self.assertEqual(len(lines), 4)
        # 2.0 KiB
        self.assertIn("2.0 KiB", body)

    def test_long_filename_truncated(self):
        body = _format_box(
            name="a" * 200,
            size=1024,
            source="",
            location="",
            duration="",
        )
        # Truncated to 36 chars + ellipsis.
        self.assertIn("…", body)
        # Original 200-char name must NOT appear in full.
        self.assertNotIn("a" * 200, body)

    def test_long_location_truncated(self):
        body = _format_box(
            name="x.mp4",
            size=1024,
            source="",
            location="rclone://" + "a" * 200,
            duration="",
        )
        self.assertIn("…", body)
        self.assertNotIn("a" * 200, body)

    def test_html_escaping_in_box(self):
        body = _format_box(
            name="<script>alert(1)</script>.mp4",
            size=1024,
            source="gofile",
            location="",
            duration="",
        )
        # The body is wrapped in <pre>...</pre>; we must not
        # let an unescaped ``<script>`` slip through.
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        # The closing pre is still present.
        self.assertTrue(body.endswith("</pre>"))


class KvFormatTests(unittest.TestCase):
    def test_basic_kv(self):
        body = _format_kv(
            name="file.mp4",
            size=4 * 1024 ** 3,
            source="bunkrr.su",
            location="rclone://x",
            duration="1m12s",
        )
        # 6 lines: status / filename / size / source / location / duration
        self.assertEqual(len(body.split("\n")), 6)
        self.assertIn("状态：✅ 上传成功", body)
        self.assertIn("文件名：file.mp4", body)
        self.assertIn("文件大小：4.0 GiB", body)
        self.assertIn("来源：bunkrr.su", body)
        self.assertIn("位置：rclone://x", body)
        self.assertIn("耗时：1m12s", body)

    def test_omits_optional_rows(self):
        body = _format_kv(
            name="x.mp4",
            size=1024,
            source="",
            location="",
            duration="",
        )
        # 3 lines: status / filename / size.
        lines = body.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertNotIn("来源", body)
        self.assertNotIn("位置", body)
        self.assertNotIn("耗时", body)


class NotificationStyleDispatchTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("TG_NOTIFY_STYLE")

    def tearDown(self):
        _set_style(self._saved)

    def test_default_is_box(self):
        _set_style(None)
        ev = {"file_name": "a.mp4", "size_bytes": 1024}
        out = _format_notification(ev)
        self.assertTrue(out.startswith("<pre>"))
        self.assertTrue(out.endswith("</pre>"))

    def test_explicit_box(self):
        _set_style("box")
        out = _format_notification({"file_name": "a.mp4", "size_bytes": 1024})
        self.assertTrue(out.startswith("<pre>"))

    def test_kv_style(self):
        _set_style("kv")
        out = _format_notification({"file_name": "a.mp4", "size_bytes": 1024})
        self.assertIn("状态：✅ 上传成功", out)
        self.assertFalse(out.startswith("<pre>"))

    def test_inline_style_legacy(self):
        _set_style("inline")
        out = _format_notification(
            {"file_name": "a.mp4", "size_bytes": 4 * 1024 ** 3}
        )
        # Legacy format with a success prefix: "✅ name — size"
        self.assertEqual(out, "✅ a.mp4 — 4.0 GiB")

    def test_unknown_style_falls_back_to_box(self):
        _set_style("garbage")
        out = _format_notification({"file_name": "a.mp4", "size_bytes": 1024})
        self.assertTrue(out.startswith("<pre>"))


class NotificationFieldRenderingTests(unittest.TestCase):
    def test_includes_source_site(self):
        _set_style("kv")
        out = _format_notification(
            {
                "file_name": "x.mp4",
                "size_bytes": 1024,
                "source_site": "gofile.io",
            }
        )
        self.assertIn("来源：gofile.io", out)

    def test_includes_duration(self):
        _set_style("kv")
        out = _format_notification(
            {
                "file_name": "x.mp4",
                "size_bytes": 1024,
                "duration_seconds": 75,
            }
        )
        self.assertIn("耗时：1m15s", out)

    def test_omits_zero_duration(self):
        _set_style("kv")
        out = _format_notification(
            {
                "file_name": "x.mp4",
                "size_bytes": 1024,
                "duration_seconds": 0,
            }
        )
        self.assertNotIn("耗时", out)

    def test_handles_missing_fields(self):
        # Event from an old publisher (no source_site /
        # duration_seconds) should still render cleanly.
        _set_style("kv")
        out = _format_notification({"file_name": "x.mp4", "size_bytes": 1024})
        self.assertIn("状态：✅ 上传成功", out)
        self.assertIn("文件名：x.mp4", out)
        self.assertIn("文件大小：1.0 KiB", out)

    def test_failure_renders_failure_status(self):
        _set_style("kv")
        out = _format_notification(
            {
                "file_name": "x.mp4",
                "size_bytes": 1024,
                "ok": False,
                "reason": "rclone: connection reset",
            }
        )
        self.assertIn("状态：❌ 上传失败", out)
        self.assertIn("原因：rclone: connection reset", out)

    def test_failure_renders_failure_status_box(self):
        body = _format_box(
            name="x.mp4",
            size=1024,
            source="",
            location="",
            duration="",
            ok=False,
            reason="rclone: connection reset",
        )
        # Body wrapped in <pre>; reason should appear as a
        # row with the literal text.
        self.assertIn("❌ 上传失败", body)
        self.assertIn("rclone: connection reset", body)

    def test_explicit_ok_true_is_treated_as_success(self):
        # Pass ``ok=True`` explicitly to make sure the
        # default-doesn't-break-the-truthy-string branch
        # works correctly.
        _set_style("kv")
        out = _format_notification(
            {"file_name": "x.mp4", "size_bytes": 1024, "ok": True}
        )
        self.assertIn("状态：✅ 上传成功", out)


class RetryButtonTests(unittest.TestCase):
    """Cover the inline retry button on failure notifications.

    The button is the operator's manual escape hatch when
    the auto-retry budget is exhausted (see
    ``MEDIA_SHUTTLE_MAX_RETRIES`` in core). Each test
    below pins one branch of the phase-to-label mapping.
    """

    def test_success_event_has_no_retry_button(self):
        # A successful upload must not render a retry
        # button — it would just tempt the operator to
        # re-run a task that already finished.
        button = _build_retry_button(
            {"file_name": "x.mp4", "size_bytes": 1024, "ok": True, "task_id": "t-1"}
        )
        self.assertIsNone(button)

    def test_missing_ok_treated_as_success(self):
        # Legacy events buffered on the redis queue from
        # an older publisher may not set ``ok``. We treat
        # those as success so the operator does not get a
        # spurious retry button on a finalized event.
        button = _build_retry_button({"file_name": "x.mp4", "task_id": "t-1"})
        self.assertIsNone(button)

    def test_download_failure_renders_dl_button(self):
        button = _build_retry_button(
            {
                "file_name": "x.mp4",
                "ok": False,
                "phase": "download",
                "task_id": "t-1",
                "reason": "bunkr 404",
            }
        )
        self.assertIsNotNone(button)
        self.assertEqual(button["text"], "🔁 重试下载")
        self.assertEqual(button["callback_data"], "retry_dl:t-1")

    def test_upload_failure_renders_ul_button(self):
        button = _build_retry_button(
            {
                "file_name": "x.mp4",
                "ok": False,
                "phase": "upload",
                "task_id": "t-2",
                "reason": "rclone connection reset",
            }
        )
        self.assertIsNotNone(button)
        self.assertEqual(button["text"], "🔁 重试上传")
        self.assertEqual(button["callback_data"], "retry_ul:t-2")

    def test_forum_failure_renders_forum_button(self):
        # Forum tasks use a different label because the
        # action is "re-walk the thread", not
        # "re-fetch a single file".
        button = _build_retry_button(
            {
                "file_name": "[forum]",
                "ok": False,
                "phase": "forum",
                "task_id": "t-3",
                "reason": "lxml parse error",
            }
        )
        self.assertIsNotNone(button)
        self.assertEqual(button["text"], "🔁 重试抓取")
        self.assertEqual(button["callback_data"], "retry_fr:t-3")

    def test_unknown_phase_renders_generic_button(self):
        # Future phase types should still get a button so
        # the operator is never stranded without an
        # escape hatch. We use the ``all`` callback token
        # so the bot can fall back to the default handler
        # without parsing a phase it doesn't recognize.
        button = _build_retry_button(
            {"file_name": "x.mp4", "ok": False, "phase": "warp_drive", "task_id": "t-4"}
        )
        self.assertIsNotNone(button)
        self.assertEqual(button["text"], "🔁 重试")
        self.assertEqual(button["callback_data"], "retry_all:t-4")

    def test_missing_phase_or_task_id_skips_button(self):
        # We need both pieces of routing info to wire
        # the button correctly. Missing either drops the
        # button rather than rendering one that would
        # 404 on click.
        self.assertIsNone(
            _build_retry_button({"file_name": "x.mp4", "ok": False, "task_id": "t-5"})
        )
        self.assertIsNone(
            _build_retry_button(
                {"file_name": "x.mp4", "ok": False, "phase": "download"}
            )
        )

    def test_callback_data_stays_under_telegram_64_byte_limit(self):
        # Telegram caps callback_data at 64 bytes. A
        # full-length uuid (36 chars) plus the prefix
        # (8 chars) is 48 bytes — well under — but we
        # pin the budget in case the prefix grows in
        # the future.
        task_id = "abcdef01-2345-6789-abcd-ef0123456789"
        button = _build_retry_button(
            {"file_name": "x.mp4", "ok": False, "phase": "upload", "task_id": task_id}
        )
        self.assertIsNotNone(button)
        self.assertLessEqual(len(button["callback_data"].encode("utf-8")), 64)

    def test_build_retry_markup_returns_none_for_success(self):
        # The pyrogram layer expects ``None`` (not an
        # empty markup) when there's no button so the
        # kwarg is dropped on the call site.
        self.assertIsNone(_build_retry_markup({"file_name": "x.mp4", "ok": True}))

    def test_parse_ok_handles_string_truthy_variants(self):
        # The redis buffer can deserialize a string
        # ``"false"`` or ``"0"`` if a publisher writes
        # one. We treat those the same as Python
        # ``False``.
        self.assertTrue(_parse_ok(True))
        self.assertTrue(_parse_ok("true"))
        self.assertTrue(_parse_ok("yes"))
        self.assertFalse(_parse_ok(False))
        self.assertFalse(_parse_ok("false"))
        self.assertFalse(_parse_ok("0"))
        self.assertFalse(_parse_ok("failed"))


if __name__ == "__main__":
    unittest.main()
