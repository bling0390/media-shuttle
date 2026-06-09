from __future__ import annotations

import unittest
from unittest.mock import patch

from core.providers.parsers_sites import gofile
from core.providers.parsers_sites.gofile import (
    _gofile_extract_id,
    _gofile_get_token,
    _gofile_list_sources,
    parse_gofile_live,
)


class GofileExtractIdTests(unittest.TestCase):
    def test_d_share_url(self) -> None:
        self.assertEqual(_gofile_extract_id("https://gofile.io/d/CgT9zm"), "CgT9zm")

    def test_contents_url(self) -> None:
        self.assertEqual(_gofile_extract_id("https://gofile.io/contents/CgT9zm"), "CgT9zm")

    def test_empty_path(self) -> None:
        self.assertIsNone(_gofile_extract_id("https://gofile.io/"))

    def test_trailing_slash(self) -> None:
        self.assertEqual(_gofile_extract_id("https://gofile.io/d/CgT9zm/"), "CgT9zm")


class GofileGetTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        # Drop any cached module-level token between tests.
        gofile._GOFILE_TOKEN = ""
        gofile._GOFILE_TOKEN_EXPIRES_AT = 0

    def test_uses_env_token_when_set(self) -> None:
        with patch.dict("os.environ", {"MEDIA_SHUTTLE_GOFILE_TOKEN": "env-jwt-abc"}):
            self.assertEqual(_gofile_get_token(), "env-jwt-abc")

    def test_calls_accounts_endpoint_when_no_env(self) -> None:
        with patch.dict("os.environ", {}, clear=False), \
             patch.object(gofile, "http_json", return_value={
                 "status": "ok",
                 "data": {"token": "guest-jwt-xyz"},
             }) as mock_http:
            token = _gofile_get_token()
            self.assertEqual(token, "guest-jwt-xyz")
            self.assertEqual(mock_http.call_count, 1)
            called_url = mock_http.call_args[0][0]
            self.assertEqual(called_url, "https://api.gofile.io/accounts")
            self.assertEqual(mock_http.call_args.kwargs.get("method"), "POST")

    def test_raises_on_non_ok_status(self) -> None:
        with patch.object(gofile, "http_json", return_value={"status": "error", "data": {}}):
            with self.assertRaises(RuntimeError) as ctx:
                _gofile_get_token()
            self.assertIn("guest account", str(ctx.exception))

    def test_raises_on_missing_token_field(self) -> None:
        with patch.object(gofile, "http_json", return_value={"status": "ok", "data": {}}):
            with self.assertRaises(RuntimeError) as ctx:
                _gofile_get_token()
            self.assertIn("missing data.token", str(ctx.exception))


class GofileListSourcesFileTests(unittest.TestCase):
    def setUp(self) -> None:
        gofile._GOFILE_TOKEN = ""

    def test_single_file_returns_one_source(self) -> None:
        with patch.object(gofile, "http_json", return_value={
            "status": "ok",
            "data": {
                "id": "CgT9zm",
                "type": "file",
                "name": "video.mp4",
                "link": "https://store-eu-1.gofile.io/download/web/CgT9zm/video.mp4?token=xyz",
            },
        }) as mock_http:
            sources = _gofile_list_sources("CgT9zm", token="xyz")
        self.assertEqual(len(sources), 1)
        src = sources[0]
        self.assertEqual(src.site, "GOFILE")
        self.assertEqual(src.file_name, "video.mp4")
        self.assertEqual(src.download_url, "https://store-eu-1.gofile.io/download/web/CgT9zm/video.mp4?token=xyz")
        self.assertEqual(src.metadata["resource_id"], "CgT9zm")
        self.assertEqual(src.metadata["token"], "xyz")
        # Verify the request went to the v2 contents endpoint with bearer auth.
        self.assertIn("/contents/CgT9zm", mock_http.call_args[0][0])
        headers = mock_http.call_args.kwargs.get("headers", {})
        self.assertEqual(headers.get("Authorization"), "Bearer xyz")
        # v2 must NOT include the v1 X-Website-Token / X-BL defenses.
        self.assertNotIn("X-Website-Token", headers)
        self.assertNotIn("X-BL", headers)

    def test_non_ok_response_returns_empty(self) -> None:
        with patch.object(gofile, "http_json", return_value={"status": "error", "data": {}}):
            self.assertEqual(_gofile_list_sources("CgT9zm", token="xyz"), [])

    def test_missing_link_returns_empty(self) -> None:
        with patch.object(gofile, "http_json", return_value={
            "status": "ok",
            "data": {"id": "CgT9zm", "type": "file", "name": "video.mp4", "link": ""},
        }):
            self.assertEqual(_gofile_list_sources("CgT9zm", token="xyz"), [])


class GofileListSourcesFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        gofile._GOFILE_TOKEN = ""

    def test_folder_recurses_into_children(self) -> None:
        def fake_http(url, *args, **kwargs):
            if "/contents/root1" in url:
                return {
                    "status": "ok",
                    "data": {
                        "id": "root1",
                        "type": "folder",
                        "name": "My Album",
                        "children": {
                            "file1": {
                                "id": "file1", "type": "file",
                                "name": "ep1.mp4",
                                "link": "https://store-eu-1.gofile.io/download/web/file1/ep1.mp4?t=a",
                            },
                            "sub1": {
                                "id": "sub1", "type": "folder", "canAccess": True,
                                "name": "Sub",
                            },
                            "skip1": {
                                "id": "skip1", "type": "unknown",
                            },
                        },
                    },
                }
            if "/contents/sub1" in url:
                return {
                    "status": "ok",
                    "data": {
                        "id": "sub1",
                        "type": "folder",
                        "name": "Sub",
                        "children": {
                            "file2": {
                                "id": "file2", "type": "file",
                                "name": "ep2.mp4",
                                "link": "https://store-eu-1.gofile.io/download/web/file2/ep2.mp4?t=b",
                            },
                        },
                    },
                }
            raise AssertionError(f"unexpected url: {url}")

        with patch.object(gofile, "http_json", side_effect=fake_http):
            sources = _gofile_list_sources("root1", token="xyz")

        names = sorted(s.file_name for s in sources)
        self.assertEqual(names, ["ep1.mp4", "ep2.mp4"])
        for src in sources:
            self.assertEqual(src.metadata["token"], "xyz")

    def test_inaccessible_folder_is_skipped(self) -> None:
        with patch.object(gofile, "http_json", return_value={
            "status": "ok",
            "data": {
                "id": "root1",
                "type": "folder",
                "name": "Locked",
                "children": {
                    "locked": {"id": "locked", "type": "folder", "canAccess": False},
                },
            },
        }):
            self.assertEqual(_gofile_list_sources("root1", token="xyz"), [])


class GofileParseLiveIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        gofile._GOFILE_TOKEN = ""

    def test_parse_live_wires_url_to_token_to_list(self) -> None:
        with patch.dict("os.environ", {"MEDIA_SHUTTLE_GOFILE_TOKEN": "env-jwt"}), \
             patch.object(gofile, "_gofile_list_sources", return_value=[]) as mock_list:
            sources = parse_gofile_live("https://gofile.io/d/CgT9zm")
        self.assertEqual(sources, [])
        mock_list.assert_called_once_with("CgT9zm", token="env-jwt")

    def test_parse_live_returns_empty_when_no_content_id(self) -> None:
        self.assertEqual(parse_gofile_live("https://gofile.io/"), [])


if __name__ == "__main__":
    unittest.main()
