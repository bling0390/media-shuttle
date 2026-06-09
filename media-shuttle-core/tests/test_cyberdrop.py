"""Unit tests for the cyberdrop parser.

The live parser hits ``api.cyberdrop.cr/api/file/auth/<id>`` and
``api.cyberdrop.cr/api/file/info/<id>`` to upgrade a page URL to a
signed CDN URL. The album parser additionally walks the album HTML
for child ``/f/<id>`` links and resolves each one through the same
auth API. All HTTP is mocked; the tests pin the documented behaviour
so a future site or API change is caught immediately.

Run with:
    cd media-shuttle-core && python -m unittest tests.test_cyberdrop
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.providers.parsers_sites.cyberdrop import (
    is_cyberdrop,
    is_cyberdrop_album,
    parse_cyberdrop_album_live,
    parse_cyberdrop_live,
)


class CyberdropMatcherTests(unittest.TestCase):
    def test_is_cyberdrop_matches_cyberdrop_me(self) -> None:
        self.assertTrue(is_cyberdrop("https://cyberdrop.me/f/abc"))

    def test_is_cyberdrop_matches_cyberdrop_cr(self) -> None:
        self.assertTrue(is_cyberdrop("https://cyberdrop.cr/f/abc"))

    def test_is_cyberdrop_matches_cyberdrop_subdomains(self) -> None:
        self.assertTrue(is_cyberdrop("https://api.cyberdrop.cr/anything"))
        self.assertTrue(is_cyberdrop("https://cdn.cyberdrop.to/anything"))

    def test_is_cyberdrop_rejects_unrelated_hosts(self) -> None:
        self.assertFalse(is_cyberdrop("https://bunkr.su/f/abc"))
        self.assertFalse(is_cyberdrop("https://gofile.io/d/abc"))

    def test_is_cyberdrop_album_requires_a_path(self) -> None:
        self.assertTrue(is_cyberdrop_album("https://cyberdrop.cr/a/album1"))
        self.assertFalse(is_cyberdrop_album("https://cyberdrop.cr/f/file1"))
        self.assertFalse(is_cyberdrop_album("https://bunkr.su/a/album1"))


class CyberdropSingleFileTests(unittest.TestCase):
    def test_returns_empty_when_no_file_id(self) -> None:
        self.assertEqual(parse_cyberdrop_live("https://cyberdrop.cr/"), [])

    def test_resolves_signed_url_and_uses_info_name(self) -> None:
        url = "https://cyberdrop.cr/f/abc123"
        info_payload = {"name": "Holiday.mkv"}
        auth_payload = {"url": "https://cdn.cyberdrop.cr/d/abc123?token=xyz"}
        with patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", return_value=info_payload), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", return_value=auth_payload["url"]
        ):
            sources = parse_cyberdrop_live(url)
        self.assertEqual(len(sources), 1)
        src = sources[0]
        self.assertEqual(src.site, "CYBERDROP")
        self.assertEqual(src.page_url, url)
        self.assertEqual(src.download_url, auth_payload["url"])
        self.assertEqual(src.file_name, "Holiday.mkv")
        self.assertEqual(src.remote_folder, "Holiday.mkv")
        self.assertTrue(src.metadata.get("resolved_live"))
        self.assertEqual(src.metadata.get("file_id"), "abc123")

    def test_falls_back_to_id_when_info_endpoint_is_unreachable(self) -> None:
        url = "https://cyberdrop.cr/f/abc123"
        with patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", return_value=None), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", return_value="https://cdn.cyberdrop.cr/d/abc123?token=xyz"
        ):
            sources = parse_cyberdrop_live(url)
        self.assertEqual(len(sources), 1)
        # safe_name treats the file id as a usable name; the explicit
        # ``cyberdrop_<id>.bin`` fallback only kicks in when the id
        # sanitizes to empty. We still expect ``remote_folder`` to be
        # ``None`` because no display name came back.
        self.assertEqual(sources[0].file_name, "abc123")
        self.assertIsNone(sources[0].remote_folder)

    def test_returns_empty_when_signed_url_endpoint_fails(self) -> None:
        url = "https://cyberdrop.cr/f/abc123"
        with patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", return_value={"name": "X.mkv"}), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", return_value=None
        ):
            self.assertEqual(parse_cyberdrop_live(url), [])

    def test_accepts_e_path_form(self) -> None:
        # The site also serves /e/<id> for embedded shares.
        url = "https://cyberdrop.cr/e/abc123"
        with patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", return_value={"name": "x.mkv"}), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", return_value="https://cdn/x?token=y"
        ):
            sources = parse_cyberdrop_live(url)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].metadata["file_id"], "abc123")


class CyberdropRequestHelpersTests(unittest.TestCase):
    def test_request_signed_url_returns_url_on_success(self) -> None:
        from core.providers.parsers_sites.cyberdrop import _cyberdrop_request_signed_url
        with patch("core.providers.parsers_sites.cyberdrop.http_json", return_value={"url": "https://cdn/a?token=b"}):
            self.assertEqual(_cyberdrop_request_signed_url("abc"), "https://cdn/a?token=b")

    def test_request_signed_url_returns_none_on_500(self) -> None:
        from core.providers.parsers_sites.cyberdrop import _cyberdrop_request_signed_url
        with patch("core.providers.parsers_sites.cyberdrop.http_json", return_value={"error": "Failed to generate signed URL"}):
            self.assertIsNone(_cyberdrop_request_signed_url("abc"))

    def test_request_signed_url_returns_none_on_exception(self) -> None:
        from core.providers.parsers_sites.cyberdrop import _cyberdrop_request_signed_url
        with patch("core.providers.parsers_sites.cyberdrop.http_json", side_effect=RuntimeError("net")):
            self.assertIsNone(_cyberdrop_request_signed_url("abc"))

    def test_request_file_info_returns_dict(self) -> None:
        from core.providers.parsers_sites.cyberdrop import _cyberdrop_request_file_info
        with patch("core.providers.parsers_sites.cyberdrop.http_json", return_value={"name": "x.mkv"}):
            self.assertEqual(_cyberdrop_request_file_info("abc"), {"name": "x.mkv"})

    def test_request_file_info_returns_none_on_404(self) -> None:
        from core.providers.parsers_sites.cyberdrop import _cyberdrop_request_file_info
        with patch("core.providers.parsers_sites.cyberdrop.http_json", return_value="Not Found"):
            self.assertIsNone(_cyberdrop_request_file_info("abc"))


class CyberdropAlbumTests(unittest.TestCase):
    def test_returns_empty_when_no_links(self) -> None:
        html = "<html><body>nothing here</body></html>"
        with patch("core.providers.parsers_sites.cyberdrop.http_text", return_value=html):
            self.assertEqual(parse_cyberdrop_album_live("https://cyberdrop.cr/a/album1"), [])

    def test_resolves_each_child_via_auth_api(self) -> None:
        html = """
        <a id="file" href="/f/child1">x</a>
        <a id="file" href="/f/child2">y</a>
        """
        info_by_id = {"child1": {"name": "one.mkv"}, "child2": {"name": "two.mkv"}}
        signed_by_id = {
            "child1": "https://cdn/child1?token=1",
            "child2": "https://cdn/child2?token=2",
        }
        def fake_info(fid: str) -> dict:
            return info_by_id.get(fid) or {}
        def fake_signed(fid: str) -> str | None:
            return signed_by_id.get(fid)
        with patch("core.providers.parsers_sites.cyberdrop.http_text", return_value=html), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", side_effect=fake_info
        ), patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", side_effect=fake_signed):
            sources = parse_cyberdrop_album_live("https://cyberdrop.cr/a/album1")
        self.assertEqual(len(sources), 2)
        # Both children get the signed CDN URL, not the page URL.
        for src in sources:
            self.assertTrue(src.download_url.startswith("https://cdn/"))
            self.assertIn("token=", src.download_url)
            # remote_folder is the album id (not the file name), so the
            # 115 path nests under the album.
            self.assertEqual(src.remote_folder, "album1")
            self.assertTrue(src.metadata.get("resolved_live"))

    def test_falls_back_to_page_url_when_signed_url_fails(self) -> None:
        html = '<a id="file" href="/f/badchild">x</a>'
        with patch("core.providers.parsers_sites.cyberdrop.http_text", return_value=html), patch(
            "core.providers.parsers_sites.cyberdrop._cyberdrop_request_file_info", return_value={}
        ), patch("core.providers.parsers_sites.cyberdrop._cyberdrop_request_signed_url", return_value=None):
            sources = parse_cyberdrop_album_live("https://cyberdrop.cr/a/album1")
        self.assertEqual(len(sources), 1)
        src = sources[0]
        self.assertEqual(src.download_url, "https://cyberdrop.cr/f/badchild")
        self.assertEqual(src.remote_folder, "album1")
        self.assertFalse(src.metadata.get("resolved_live"))

    def test_returns_empty_on_html_fetch_failure(self) -> None:
        with patch("core.providers.parsers_sites.cyberdrop.http_text", side_effect=RuntimeError("ddg")):
            self.assertEqual(parse_cyberdrop_album_live("https://cyberdrop.cr/a/album1"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
