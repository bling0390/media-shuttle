"""Unit tests for the bunkr parser.

These tests cover the fixed pieces of bunkr.py without making any network
calls - the `http_text`/`http_json` helpers are mocked. We also pin
documented behaviour (e.g. HTML entity decoding, ogname as filename,
folder name = title minus extension) so a future bunkr site change is
caught immediately.

Run with:
    cd media-shuttle-core && python -m unittest tests.test_bunkr
"""

from __future__ import annotations

import unittest
from unittest import mock

from core.providers.parsers_sites import bunkr as bunkr_mod
from core.providers.parsers_sites.bunkr import (
    _bunkr_file_id_from_html,
    _bunkr_file_name,
    _bunkr_folder_name,
    _bunkr_resolve_single_file_download_url,
    is_bunkr,
    is_bunkr_album,
    parse_bunkr,
    parse_bunkr_live,
)


SINGLE_HTML = """\
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta property="og:image" content="https://ino2.scdn.st/thumbs/6b8fa397-9f97-4407-9a6f-83b5fde4eb13.png">
    <title>Download How to Lick Pussy Right Let Lana &amp; Luna Teach You.mp4</title>
    <script>var ogname = "How to Lick Pussy Right Let Lana \u0026 Luna Teach You.mp4";</script>
    <script defer data-file-id="57930011" src="../js/lv.js"></script>
</head>
<body>
    <h1 class="truncate">How to Lick Pussy Right Let Lana &amp; Luna Teach You.mp4</h1>
    <p class="mt-1 text-xs">234.43 MB</p>
    <a class="btn btn-main btn-lg rounded-full px-6 font-semibold ic-download-01 ic-before before:text-lg" href="https://dl.bunkr.cr/file/57930011">Download</a>
</body>
</html>
"""


ALBUM_PAGE_1_HTML = """\
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <title>ANNA_APPLE21 (あんなのお部屋) | Bunkr</title>
</head>
<body>
    <h1 class="truncate">ANNA_APPLE21 (あんなのお部屋)</h1>
    <a class="grid-images" href="/f/pTuUCwqaXvRBu"></a>
</body>
</html>
"""


SINGLE_CHILD_HTML = """\
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <title>57 - 670f4202-03f4-4c69-9d91-d41510e6c7bb.JPG | Bunkr</title>
    <script defer data-file-id="99990001" src="../js/lv.js"></script>
</head>
<body>
    <h1 class="truncate">57 - 670f4202-03f4-4c69-9d91-d41510e6c7bb.JPG</h1>
    <a class="btn ic-download-01" href="https://dl.bunkr.cr/file/99990001">Download</a>
</body>
</html>
"""


# A second child page where the `<title>` lacks the file extension -
# ensures the parser still falls back to a sensible file_name.
SINGLE_CHILD_HTML_TITLE_ONLY = """\
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <title>Random Snapshot</title>
    <script defer data-file-id="99990002" src="../js/lv.js"></script>
</head>
<body>
    <h1 class="truncate">Random Snapshot</h1>
    <a class="btn ic-download-01" href="https://dl.bunkr.cr/file/99990002">Download</a>
</body>
</html>
"""


def _mock_001_v2(file_id: str) -> dict:
    return {
        "mediafiles": "https://c1sp-b.cdn.cr",
        "original": "",  # no original filename => parser omits &n=
        "path": f"/storage/media/{file_id}.bin",
    }


def _mock_sign(path: str) -> dict:
    return {"token": "FAKE-TOKEN", "ex": "9999999999"}


class BunkrHelpersTests(unittest.TestCase):
    """Direct unit tests of the small regex/parser helpers."""

    def test_file_id_extracted_from_data_file_id(self) -> None:
        self.assertEqual(_bunkr_file_id_from_html(SINGLE_HTML), "57930011")

    def test_file_id_extracted_from_data_id(self) -> None:
        # dl.bunkr.cr wrapper uses data-id, not data-file-id.
        html = '<a id="download-btn" data-id="12345" href="#">'
        self.assertEqual(_bunkr_file_id_from_html(html), "12345")

    def test_file_id_returns_empty_when_absent(self) -> None:
        self.assertEqual(_bunkr_file_id_from_html("<html></html>"), "")

    def test_folder_name_decodes_html_entities(self) -> None:
        # Title contains "&amp;" which must come out as "&".
        got = _bunkr_folder_name(SINGLE_HTML)
        self.assertEqual(got, "How to Lick Pussy Right Let Lana & Luna Teach You.mp4")

    def test_folder_name_strips_download_prefix(self) -> None:
        html = "<title>Download Some Movie.mp4</title>"
        # The parser strips a leading "Download " from the title.
        self.assertEqual(_bunkr_folder_name(html), "Some Movie.mp4")

    def test_file_name_uses_ogname(self) -> None:
        self.assertEqual(
            _bunkr_file_name(SINGLE_HTML),
            "How to Lick Pussy Right Let Lana & Luna Teach You.mp4",
        )

    def test_file_name_falls_back(self) -> None:
        self.assertEqual(_bunkr_file_name("<html></html>"), "file.bin")


class BunkrClassifierTests(unittest.TestCase):
    def test_is_bunkr_matches_known_hosts(self) -> None:
        for url in (
            "https://bunkr.cr/f/abc",
            "https://bunkr.site/v/abc",
            "https://bunkr.albums.io/a/abc",
            "https://cdn.bunkrr.su/x",
        ):
            self.assertTrue(is_bunkr(url), url)

    def test_is_bunkr_rejects_others(self) -> None:
        for url in ("https://example.com/bunkr", "https://pixeldrain.com/u/abc"):
            self.assertFalse(is_bunkr(url), url)

    def test_is_bunkr_album_only_for_a_path(self) -> None:
        self.assertTrue(is_bunkr_album("https://bunkr.cr/a/abc"))
        self.assertFalse(is_bunkr_album("https://bunkr.cr/f/abc"))
        self.assertFalse(is_bunkr_album("https://bunkr.cr/v/abc"))


class BunkrResolveSingleFileUrlTests(unittest.TestCase):
    """Drive _bunkr_resolve_single_file_download_url with mocked HTTP."""

    def test_resolve_via_data_file_id_and_sign_api(self) -> None:
        # http_text is called once (for the page); http_json is called
        # twice (api/_001_v2, then glb-apisign/sign).
        with mock.patch.object(bunkr_mod, "http_text", return_value=SINGLE_HTML) as p_text, \
             mock.patch.object(bunkr_mod, "http_json", side_effect=[
                 _mock_001_v2("57930011"),  # /api/_001_v2
                 _mock_sign("/storage/media/57930011.bin"),  # /sign
             ]) as p_json:
            got = _bunkr_resolve_single_file_download_url(
                "https://bunkr.cr/f/xkdwMPFHh372y", SINGLE_HTML
            )

        self.assertEqual(
            got,
            "https://c1sp-b.cdn.cr/storage/media/57930011.bin?token=FAKE-TOKEN&ex=9999999999",
        )
        # We must NOT re-fetch the page itself (it's already in `html`).
        p_text.assert_not_called()
        self.assertEqual(p_json.call_count, 2)

    def test_resolve_falls_back_to_wrapper_when_no_file_id(self) -> None:
        # A page without data-file-id/data-id falls through. There's no
        # <video>/<source> here either, so we get "".
        no_id_html = "<html><title>Foo</title><body>bar</body></html>"
        with mock.patch.object(bunkr_mod, "http_text", return_value=no_id_html) as p_text, \
             mock.patch.object(bunkr_mod, "http_json", return_value={}) as p_json:
            got = _bunkr_resolve_single_file_download_url(
                "https://bunkr.cr/f/whatever", no_id_html
            )
        self.assertEqual(got, "")
        p_text.assert_not_called()
        p_json.assert_not_called()


class BunkrParseSinglePageTests(unittest.TestCase):
    """parse_bunkr_live on a /f/ URL produces a single ParsedSource
    with the resolved CDN URL, the ogname as file_name, and a folder
    name that's the title minus its file-extension suffix."""

    def test_parse_single_page_full_flow(self) -> None:
        with mock.patch.object(
            bunkr_mod, "http_text", return_value=SINGLE_HTML
        ) as p_text, mock.patch.object(
            bunkr_mod, "http_json",
            side_effect=[_mock_001_v2("57930011"), _mock_sign("/storage/media/57930011.bin")],
        ):
            sources = parse_bunkr_live("https://bunkr.cr/f/xkdwMPFHh372y")

        # parse_bunkr_live fetches the page once itself; then the cdn
        # resolution only talks to the JSON APIs (no further html).
        self.assertEqual(p_text.call_count, 1)
        self.assertEqual(len(sources), 1)
        s = sources[0]
        self.assertEqual(s.site, "BUNKR")
        self.assertEqual(s.page_url, "https://bunkr.cr/f/xkdwMPFHh372y")
        # file_name is the ogname (HTML-entity decoded, NOT stripped of
        # extension - that's the original upload filename).
        self.assertEqual(
            s.file_name, "How to Lick Pussy Right Let Lana & Luna Teach You.mp4"
        )
        # remote_folder is the title sans extension.
        self.assertEqual(
            s.remote_folder, "How to Lick Pussy Right Let Lana & Luna Teach You"
        )
        # download_url is the signed CDN URL.
        self.assertEqual(
            s.download_url,
            "https://c1sp-b.cdn.cr/storage/media/57930011.bin?token=FAKE-TOKEN&ex=9999999999",
        )


class BunkrParseAlbumPageTests(unittest.TestCase):
    """parse_bunkr_live on a /a/ URL must pre-resolve every child
    /f/<slug> into a CDN URL so the downloader doesn't fall back to
    grabbing thumbnails."""

    def _album_responses(self) -> list[str | dict]:
        # Per-child page HTMLs, in the order they appear on the album.
        return [SINGLE_CHILD_HTML, SINGLE_CHILD_HTML, SINGLE_CHILD_HTML_TITLE_ONLY]

    def test_album_resolves_every_child(self) -> None:
        with mock.patch.object(
            bunkr_mod, "http_text",
            side_effect=[
                ALBUM_PAGE_1_HTML,        # 1st call: album page
                SINGLE_CHILD_HTML,        # 2nd: child 1
            ],
        ), mock.patch.object(
            bunkr_mod, "http_json",
            side_effect=[
                # Album page itself doesn't trigger API calls.
                _mock_001_v2("99990001"), _mock_sign("/storage/media/99990001.bin"),
            ],
        ):
            sources = parse_bunkr_live("https://bunkr.site/a/c52RA5Zz")

        self.assertEqual(len(sources), 1)
        # The single source must have a real CDN URL (containing 'c1sp-b.cdn.cr'),
        # not a /f/<slug> page URL.
        for s in sources:
            self.assertIn("c1sp-b.cdn.cr", s.download_url, s.download_url)
            self.assertNotIn("/f/", s.download_url)
            self.assertTrue(s.download_url.startswith("http"), s.download_url)

    def test_album_keeps_per_file_folder_and_filename(self) -> None:
        with mock.patch.object(
            bunkr_mod, "http_text",
            side_effect=[ALBUM_PAGE_1_HTML, SINGLE_CHILD_HTML],
        ), mock.patch.object(
            bunkr_mod, "http_json",
            side_effect=[_mock_001_v2("99990001"), _mock_sign("/storage/media/99990001.bin")],
        ):
            sources = parse_bunkr_live("https://bunkr.site/a/c52RA5Zz")

        self.assertEqual(len(sources), 1)
        s = sources[0]
        # Child page title was "57 - 670f4202-...JPG" - folder strips the .JPG.
        self.assertEqual(s.remote_folder, "57 - 670f4202-03f4-4c69-9d91-d41510e6c7bb")
        # No ogname on the child page, so file_name falls back to the title.
        self.assertEqual(s.file_name, "57 - 670f4202-03f4-4c69-9d91-d41510e6c7bb.JPG")


class BunkrParseFallbackTests(unittest.TestCase):
    """parse_bunkr (no `_live` suffix) is the static fallback that
    produces a single source pointing at the URL itself."""

    def test_parse_bunkr_static(self) -> None:
        sources = parse_bunkr("https://bunkr.cr/v/abc123")
        self.assertEqual(len(sources), 1)
        s = sources[0]
        self.assertEqual(s.site, "BUNKR")
        self.assertEqual(s.page_url, "https://bunkr.cr/v/abc123")
        self.assertEqual(s.download_url, "https://bunkr.cr/v/abc123")
        self.assertIn("abc123", s.file_name)


if __name__ == "__main__":
    unittest.main()
