import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.models import ParsedSource
from core.plugins.parsers import default_registry as default_parser_registry
from core.providers.downloaders_builtin import download_bunkr_live


class _FakeResponse:
    def __init__(self, payload: str | bytes | dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    @property
    def content(self) -> bytes:
        if isinstance(self._payload, bytes):
            return self._payload
        if isinstance(self._payload, dict):
            return json.dumps(self._payload).encode("utf-8")
        return self._payload.encode("utf-8")

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> dict:
        if isinstance(self._payload, dict):
            return self._payload
        text = self.text
        return json.loads(text) if text else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error: {self.status_code}")


class TestCoreLiveProviderBehavior(unittest.TestCase):
    def test_gofile_live_parser_should_fetch_token_and_real_links(self):
        def _request(method, url, **kwargs):
            _ = method, kwargs
            if url == "https://api.gofile.io/accounts":
                return _FakeResponse({"status": "ok", "data": {"token": "tok-live"}})
            if url.startswith("https://api.gofile.io/contents/ABC123"):
                return _FakeResponse(
                    {
                        "status": "ok",
                        "data": {
                            "type": "folder",
                            "name": "ABC123",
                            "children": {
                                "file-1": {
                                    "type": "file",
                                    "name": "sample.mp4",
                                    "link": "https://store.gofile.io/download/web/FILE-1/sample.mp4",
                                }
                            },
                        },
                    }
                )
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.parsers_sites.gofile._GOFILE_TOKEN", ""), patch(
            "core.providers.parsers_sites.gofile._GOFILE_TOKEN_EXPIRES_AT", 0
        ), patch("core.providers.parsers_sites.common.httpx.request", side_effect=_request):
            registry = default_parser_registry(mode="live")
            sources = registry.parse("https://gofile.io/d/ABC123")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "GOFILE")
        self.assertEqual(sources[0].download_url, "https://store.gofile.io/download/web/FILE-1/sample.mp4")
        self.assertEqual(sources[0].metadata.get("token"), "tok-live")

    def test_bunkr_live_downloader_should_resolve_actual_link(self):
        source = ParsedSource(
            site="BUNKR",
            page_url="https://bunkr.sk/f/abc123",
            download_url="https://bunkr.sk/f/abc123",
            file_name="",
            remote_folder="abc123",
        )
        actual_url = "https://media-files.bunkr.sk/videos/real-file.mp4"

        def _request(method, url, **kwargs):
            _ = method, kwargs
            if url == source.download_url:
                return _FakeResponse(f'<video src="{actual_url}"></video>')
            if url == actual_url:
                return _FakeResponse(b"video-bytes")
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.parsers_sites.common.httpx.request", side_effect=_request), patch(
            "core.providers.downloaders_sites.common.httpx.request", side_effect=_request
        ):
            result = download_bunkr_live(source)

        self.assertEqual(result.source_url, actual_url)
        self.assertEqual(result.file_name, "real-file.mp4")

    def test_bunkr_live_parser_should_expand_links_from_page_content(self):
        def _request(method, url, **kwargs):
            _ = method, kwargs
            if url == "https://bunkr.sk/a/mixed-page":
                return _FakeResponse(
                    """
                    <html><body>
                      <h1 class="truncate">Mix Set</h1>
                      <div class="relative group/item theItem"><a href="/f/file-a1">A</a></div>
                      <a href="/f/">Invalid F</a>
                      <a class="grid-images_box-link" href="/v/video-b2">B</a>
                      <a href="/a/nested-1">Nested Album</a>
                      <a href="/a/">Invalid A</a>
                    </body></html>
                    """
                )
            if url == "https://bunkr.sk/a/nested-1":
                return _FakeResponse('<a class="grid-images_box-link" href="/f/file-c3">C</a>')
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.parsers_sites.common.httpx.request", side_effect=_request):
            registry = default_parser_registry(mode="live")
            sources = registry.parse("https://bunkr.sk/a/mixed-page")

        self.assertEqual(len(sources), 3)
        self.assertEqual({item.download_url for item in sources}, {
            "https://bunkr.sk/f/file-a1",
            "https://bunkr.sk/v/video-b2",
            "https://bunkr.sk/f/file-c3",
        })
        self.assertTrue(all(item.site == "BUNKR" for item in sources))
        self.assertTrue(all(item.remote_folder == "Mix Set" for item in sources))
        self.assertTrue(all(item.file_name == "" for item in sources))

    def test_bunkr_live_parser_should_resolve_single_v_link_via_api(self):
        direct_url = "https://media-files.bunkr.sk/videos/xyz999.mp4"

        def _request(method, url, **kwargs):
            _ = method
            if url == "https://bunkr.sk/v/xyz999":
                return _FakeResponse('<h1 class="truncate">Single V</h1>')
            if url == "https://bunkr.sk/api/gimmeurl":
                self.assertEqual(kwargs.get("json"), {"slug": "xyz999"})
                return _FakeResponse({"data": {"newUrl": direct_url}})
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.parsers_sites.common.httpx.request", side_effect=_request):
            registry = default_parser_registry(mode="live")
            sources = registry.parse("https://bunkr.sk/v/xyz999")

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].download_url, direct_url)
        self.assertEqual(sources[0].page_url, "https://bunkr.sk/v/xyz999")
        self.assertEqual(sources[0].remote_folder, "Single V")

    def test_transfer_live_parser_should_fetch_metadata_but_keep_page_url(self):
        page_url = "https://transfer.it/t/tWvdyDpHkRCZ"

        def _request(method, url, **kwargs):
            _ = method
            body = kwargs.get("json")
            if url.startswith("https://g.api.mega.co.nz/cs?") and body == [{"a": "xi", "xh": "tWvdyDpHkRCZ"}]:
                return _FakeResponse(
                    json.dumps([{"t": "ODMyNDE2NzkzNDI2NDExNTIwLm1wNA", "size": [2001281384, 1, 1, 0, 0]}])
                )
            if url.startswith("https://g.api.mega.co.nz/cs?") and body == [{"a": "f", "c": 1, "r": 1, "xnc": 1}]:
                return _FakeResponse(
                    json.dumps(
                        [
                            {
                                "f": [
                                    {
                                        "h": "WSpRVDJY",
                                        "p": "",
                                        "t": 1,
                                        "a": "kMc_-MYvtHvktKR_EtKNrYS6ZXsmAUQORENQWyp5-KoLADv6ZDmyIhPRoQMUTfMPg-mkKFxq0x5hE2zkpV2dIQ",
                                        "k": "bIHn-aXqty2-oBzD25ylBg",
                                    },
                                    {
                                        "h": "KKw1zT5Z",
                                        "p": "WSpRVDJY",
                                        "t": 0,
                                        "a": "tx4PHm0S-aCYRJ1lkxZ9G874-X1W0LE_W5DUxiAw0-CFDR35mlrgMcTtqa0RIn-yRVniCnlj-iAJ9AFzG4bQu_Bwi6EhXIaqRtW5urR16ZI",
                                        "k": "LP0H0lL_yDJKEpNYLUIIexwivChb_ikzpN8t4wPUhL0",
                                        "s": 2001281384,
                                    },
                                ]
                            }
                        ]
                    )
                )
            raise AssertionError(f"unexpected url/body: {url} {body}")

        with patch("core.providers.parsers_sites.transfer.httpx.request", side_effect=_request):
            registry = default_parser_registry(mode="live")
            sources = registry.parse(page_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "TRANSFERIT")
        self.assertEqual(sources[0].page_url, page_url)
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "832416793426411520.mp4")
        self.assertEqual(sources[0].remote_folder, "tWvdyDpHkRCZ")
        self.assertEqual(sources[0].metadata.get("share_id"), "tWvdyDpHkRCZ")
        self.assertEqual(sources[0].metadata.get("node_handle"), "KKw1zT5Z")
        self.assertFalse(sources[0].metadata.get("resolved_live"))

    def test_filester_live_parser_should_fetch_metadata_but_keep_page_url(self):
        page_url = "https://filester.me/d/QHdR2xo"
        html = """
        <html>
          <head>
            <title>832416793426411520.mp4 | filester.me</title>
            <meta property="og:title" content="832416793426411520.mp4">
          </head>
          <body>
            <h1>832416793426411520.mp4</h1>
            <script>
              window.fileUUID = "6c0e217e-3d95-4acf-8d33-64c6b98f87ee";
            </script>
          </body>
        </html>
        """

        with patch("core.providers.parsers_sites.filester.http_text", return_value=html):
            registry = default_parser_registry(mode="live")
            sources = registry.parse(page_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "FILESTER")
        self.assertEqual(sources[0].page_url, page_url)
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "832416793426411520.mp4")
        self.assertEqual(sources[0].remote_folder, "6c0e217e-3d95-4acf-8d33-64c6b98f87ee")
        self.assertEqual(sources[0].metadata.get("file_slug"), "QHdR2xo")
        self.assertEqual(sources[0].metadata.get("file_uuid"), "6c0e217e-3d95-4acf-8d33-64c6b98f87ee")
        self.assertFalse(sources[0].metadata.get("resolved_live"))

    def test_turbo_live_parser_should_fetch_metadata_but_keep_page_url(self):
        page_url = "https://turbo.cr/v/gLlVkUMFwaO"
        html = """
        <html>
          <head>
            <title>gLlVkUMFwaO.mp4 — turbo.cr</title>
          </head>
          <body>
            <h1>gLlVkUMFwaO.mp4</h1>
          </body>
        </html>
        """

        with patch("core.providers.parsers_sites.turbo.http_text", return_value=html):
            registry = default_parser_registry(mode="live")
            sources = registry.parse(page_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "TURBO")
        self.assertEqual(sources[0].page_url, page_url)
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "gLlVkUMFwaO.mp4")
        self.assertEqual(sources[0].remote_folder, "gLlVkUMFwaO")
        self.assertEqual(sources[0].metadata.get("slug"), "gLlVkUMFwaO")
        self.assertFalse(sources[0].metadata.get("resolved_live"))


if __name__ == "__main__":
    unittest.main()
