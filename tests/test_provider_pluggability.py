import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))
sys.path.insert(0, str(Path("tests").resolve()))

from core.models import DownloadResult, ParsedSource, UploadResult
from core.plugins.downloaders import default_registry as default_download_registry
from core.plugins.parsers import default_registry as default_parse_registry
from core.plugins.uploaders import default_registry as default_upload_registry
from core.providers.types import DownloadProvider, ParseProvider, UploadProvider


class TestProviderPluggability(unittest.TestCase):
    def test_parser_extra_provider_should_override_fallback(self):
        custom = ParseProvider(
            name="custom",
            mode="all",
            matcher=lambda url: "custom.local" in url,
            parser=lambda url: [
                ParsedSource(
                    site="CUSTOM",
                    page_url=url,
                    download_url=url,
                    file_name="custom.bin",
                )
            ],
        )

        registry = default_parse_registry(mode="mock", extra_providers=[custom])
        parsed = registry.parse("https://custom.local/file")
        self.assertEqual(parsed[0].site, "CUSTOM")

    def test_downloader_extra_provider_should_run_in_live_mode(self):
        custom = DownloadProvider(
            name="custom-live-downloader",
            mode="live",
            matcher=lambda source: source.site == "CUSTOM",
            downloader=lambda source: DownloadResult(
                site=source.site,
                source_url=source.download_url,
                local_path="/tmp/custom-live.bin",
                size_bytes=123,
                file_name=source.file_name,
                remote_folder=source.remote_folder,
            ),
        )

        registry = default_download_registry(mode="live", extra_providers=[custom])
        result = registry.download(
            ParsedSource(
                site="CUSTOM",
                page_url="https://custom.local/file",
                download_url="https://custom.local/file",
                file_name="custom-live.bin",
            )
        )
        self.assertEqual(result.local_path, "/tmp/custom-live.bin")

    def test_uploader_extra_provider_should_run_in_live_mode(self):
        custom = UploadProvider(
            name="custom-live-uploader",
            mode="live",
            matcher=lambda target: target == "CUSTOM_TARGET",
            uploader=lambda download, destination: UploadResult(location=f"custom://{destination}/{download.file_name}"),
        )

        registry = default_upload_registry(mode="live", extra_providers=[custom])
        result = registry.upload(
            "CUSTOM_TARGET",
            DownloadResult(
                site="CUSTOM",
                source_url="https://custom.local/file",
                local_path="/tmp/custom-live.bin",
                size_bytes=123,
                file_name="custom-live.bin",
            ),
            "bucket",
        )
        self.assertEqual(result.location, "custom://bucket/custom-live.bin")

    def test_live_builtin_should_fail_for_unimplemented_sites(self):
        registry = default_download_registry(mode="live")
        with self.assertRaises(RuntimeError):
            registry.download(
                ParsedSource(
                    site="YTDL",
                    page_url="https://youtube.com/watch?v=abc",
                    download_url="https://youtube.com/watch?v=abc",
                    file_name="video.mp4",
                )
            )

    def test_extra_provider_module_should_be_loaded(self):
        parse_registry = default_parse_registry(mode="mock", extra_provider_modules=["ext_dynamic_provider"])
        parsed = parse_registry.parse("https://module.loaded/file")
        self.assertEqual(parsed[0].site, "MODULE_PARSE")

        download_registry = default_download_registry(mode="mock", extra_provider_modules=["ext_dynamic_provider"])
        downloaded = download_registry.download(parsed[0])
        self.assertEqual(downloaded.local_path, "/tmp/module-downloaded.bin")

        upload_registry = default_upload_registry(mode="mock", extra_provider_modules=["ext_dynamic_provider"])
        uploaded = upload_registry.upload("MODULE_TARGET", downloaded, "bucket")
        self.assertEqual(uploaded.location, "module://bucket/module.bin")


if __name__ == "__main__":
    unittest.main()
