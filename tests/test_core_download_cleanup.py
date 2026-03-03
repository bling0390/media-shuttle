import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.models import ParsedSource
from core.providers.downloaders_builtin import download_live_generic, download_mock


class TestCoreDownloadCleanup(unittest.TestCase):
    def test_live_download_failure_should_cleanup_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR")
            old_cleanup = os.getenv("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS")
            os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = tmp
            os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = "1"
            try:
                source = ParsedSource(
                    site="GENERIC",
                    page_url="https://example.com/file.mp4",
                    download_url="https://example.com/file.mp4",
                    file_name="file.mp4",
                )

                def _broken_download(_url, path, headers=None):
                    _ = headers
                    path.write_bytes(b"partial")
                    raise RuntimeError("network failed")

                with patch("core.providers.downloaders_sites.common.http_download", side_effect=_broken_download):
                    with self.assertRaises(RuntimeError):
                        download_live_generic(source)
            finally:
                if old_dir is None:
                    os.environ.pop("MEDIA_SHUTTLE_DOWNLOAD_DIR", None)
                else:
                    os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = old_dir
                if old_cleanup is None:
                    os.environ.pop("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = old_cleanup

            self.assertEqual(list(Path(tmp).rglob("*")), [])

    def test_mock_download_failure_should_cleanup_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR")
            old_cleanup = os.getenv("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS")
            os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = tmp
            os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = "1"
            try:
                source = ParsedSource(
                    site="GENERIC",
                    page_url="https://example.com/file.mp4",
                    download_url="https://example.com/file.mp4",
                    file_name="file.mp4",
                )

                def _broken_write(path, src):
                    _ = src
                    path.write_bytes(b"partial")
                    raise RuntimeError("disk failed")

                with patch("core.providers.downloaders_sites.common.write_mock_file", side_effect=_broken_write):
                    with self.assertRaises(RuntimeError):
                        download_mock(source)
            finally:
                if old_dir is None:
                    os.environ.pop("MEDIA_SHUTTLE_DOWNLOAD_DIR", None)
                else:
                    os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = old_dir
                if old_cleanup is None:
                    os.environ.pop("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = old_cleanup

            self.assertEqual(list(Path(tmp).rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
