import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.plugins.downloaders import default_registry as default_downloader_registry
from core.plugins.parsers import default_registry as default_parser_registry
from core.plugins.uploaders import default_registry as default_uploader_registry


class TestCoreSitePlugins(unittest.TestCase):
    def test_gofile_parser_should_identify_site(self):
        registry = default_parser_registry()
        sources = registry.parse("https://gofile.io/d/ABC123")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "GOFILE")
        self.assertIn("ABC123", sources[0].file_name)

    def test_bunkr_parser_should_identify_site(self):
        registry = default_parser_registry()
        sources = registry.parse("https://bunkr.ru/f/xyz987")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "BUNKR")
        self.assertIn("xyz987", sources[0].file_name)

    def test_mediafire_parser_should_identify_site(self):
        registry = default_parser_registry()
        sources = registry.parse("https://www.mediafire.com/file/abc/myfile.zip/file")
        self.assertEqual(sources[0].site, "MEDIAFIRE")

    def test_additional_site_parsers_should_identify_site(self):
        registry = default_parser_registry()
        cases = [
            ("https://cyberdrop.me/f/abc123", "CYBERDROP"),
            ("https://cyberfile.me/abc123", "CYBERFILE"),
            ("https://pixeldrain.com/u/xyz987", "PIXELDRAIN"),
            ("https://drive.google.com/file/d/1abcDEF123456789/view", "GD"),
            ("https://mega.nz/file/abc#key", "MEGA"),
            ("https://saint.to/embed/abc123", "SAINT"),
            ("https://coomer.su/post/123456", "COOMER"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                sources = registry.parse(url)
                self.assertGreaterEqual(len(sources), 1)
                self.assertEqual(sources[0].site, expected)

    def test_downloader_and_uploader_should_route_by_site_and_target(self):
        parser = default_parser_registry()
        downloader = default_downloader_registry()
        uploader = default_uploader_registry()

        source = parser.parse("https://gofile.io/d/ABC123")[0]
        download = downloader.download(source)
        self.assertEqual(download.site, "GOFILE")
        self.assertEqual(Path(download.local_path).name, "tmp.part")
        self.assertNotIn(source.file_name, download.local_path)

        uploaded = uploader.upload("RCLONE", download, "/incoming")
        self.assertTrue(uploaded.location.startswith("rclone://"))

    def test_downloader_should_route_additional_sites(self):
        parser = default_parser_registry()
        downloader = default_downloader_registry()
        urls = [
            "https://cyberdrop.me/f/abc123",
            "https://cyberfile.me/abc123",
            "https://pixeldrain.com/u/xyz987",
            "https://drive.google.com/file/d/1abcDEF123456789/view",
            "https://mega.nz/file/abc#key",
            "https://saint.to/embed/abc123",
            "https://coomer.su/post/123456",
        ]
        for url in urls:
            with self.subTest(url=url):
                source = parser.parse(url)[0]
                download = downloader.download(source)
                self.assertEqual(download.site, source.site)
                self.assertEqual(Path(download.local_path).name, "tmp.part")
                self.assertNotIn(source.file_name, download.local_path)


if __name__ == "__main__":
    unittest.main()
