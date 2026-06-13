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
        # so the line-by-line checks below see the box
        # borders directly.
        if body.startswith("<pre>"):
            body = body[len("<pre>"):]
        if body.endswith("</pre>"):
            body = body[: -len("</pre>")]
        lines = body.split("\n")
        # 9 lines: top + status + mid + filename + size + source +
        # location + duration + bottom.
        self.assertEqual(len(lines), 9)
        # All interior rows must share the same length.
        row_lens = {len(line) for line in lines[1:-1]}
        self.assertEqual(len(row_lens), 1, f"rows have varying lengths: {row_lens}")
        # Borders match.
        self.assertTrue(lines[0].startswith("┌"))
        self.assertTrue(lines[-1].startswith("└"))
        self.assertTrue(lines[2].startswith("├"))

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
        # 6 lines: top + status + mid + filename + size + bottom.
        self.assertEqual(len(lines), 6)
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
        self.assertIn("状态：上传成功", body)
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
        self.assertIn("状态：上传成功", out)
        self.assertFalse(out.startswith("<pre>"))

    def test_inline_style_legacy(self):
        _set_style("inline")
        out = _format_notification(
            {"file_name": "a.mp4", "size_bytes": 4 * 1024 ** 3}
        )
        # Legacy format: "name — size"
        self.assertEqual(out, "a.mp4 — 4.0 GiB")

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
        self.assertIn("状态：上传成功", out)
        self.assertIn("文件名：x.mp4", out)
        self.assertIn("文件大小：1.0 KiB", out)


if __name__ == "__main__":
    unittest.main()
