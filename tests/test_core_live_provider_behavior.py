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
    def __init__(self, payload: str | bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        if isinstance(self._payload, bytes):
            return self._payload
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class TestCoreLiveProviderBehavior(unittest.TestCase):
    def test_gofile_live_parser_should_fetch_token_and_real_links(self):
        def _urlopen(req, timeout=20):
            url = req.full_url
            if url == "https://api.gofile.io/accounts":
                return _FakeResponse(json.dumps({"status": "ok", "data": {"token": "tok-live"}}))
            if url.startswith("https://api.gofile.io/contents/ABC123"):
                return _FakeResponse(
                    json.dumps(
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
                )
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.parsers_builtin._GOFILE_TOKEN", ""), patch(
            "core.providers.parsers_builtin._GOFILE_TOKEN_EXPIRES_AT", 0
        ), patch("core.providers.parsers_builtin.urlopen", side_effect=_urlopen):
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
            file_name="bunkr_abc123.mp4",
            remote_folder="abc123",
        )
        actual_url = "https://media-files.bunkr.sk/videos/real-file.mp4"

        def _urlopen(req, timeout=20):
            url = req.full_url
            if url == source.download_url:
                return _FakeResponse(f'<video src="{actual_url}"></video>')
            if url == actual_url:
                return _FakeResponse(b"video-bytes")
            raise AssertionError(f"unexpected url: {url}")

        with patch("core.providers.downloaders_builtin.urlopen", side_effect=_urlopen):
            result = download_bunkr_live(source)

        self.assertEqual(result.source_url, actual_url)


if __name__ == "__main__":
    unittest.main()
