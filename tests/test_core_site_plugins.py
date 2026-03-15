import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.models import DownloadResult, ParsedSource
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
        self.assertEqual(sources[0].download_url, "https://www.mediafire.com/file/abc/myfile.zip/file")
        self.assertEqual(sources[0].file_name, "myfile.zip")
        self.assertEqual(sources[0].remote_folder, "abc")

    def test_mediafire_live_parser_should_not_resolve_download_link(self):
        registry = default_parser_registry(mode="live")
        page_url = "https://www.mediafire.com/file/llpowfz10s7hep8/XIUREN_No.3684.rar/file"

        with patch("core.providers.parsers_sites.mediafire.http_text") as mocked_http:
            sources = registry.parse(page_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "MEDIAFIRE")
        self.assertEqual(sources[0].page_url, page_url)
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "XIUREN_No.3684.rar")
        self.assertEqual(sources[0].remote_folder, "llpowfz10s7hep8")
        self.assertFalse(sources[0].metadata["resolved_live"])
        mocked_http.assert_not_called()

    def test_mediafire_live_parser_should_accept_existing_direct_link(self):
        registry = default_parser_registry(mode="live")
        direct_url = (
            "https://download937.mediafire.com/token/llpowfz10s7hep8/"
            "XIUREN+No.3684.rar"
        )

        sources = registry.parse(direct_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].download_url, direct_url)
        self.assertEqual(sources[0].file_name, "XIUREN_No.3684.rar")
        self.assertEqual(sources[0].remote_folder, "llpowfz10s7hep8")
        self.assertFalse(sources[0].metadata["resolved_live"])

    def test_mediafire_live_downloader_should_refresh_direct_url(self):
        downloader = default_downloader_registry(mode="live")
        page_url = "https://www.mediafire.com/file/llpowfz10s7hep8/XIUREN_No.3684.rar/file"
        direct_url = "https://download937.mediafire.com/token/llpowfz10s7hep8/XIUREN+No.3684.rar"
        source = ParsedSource(
            site="MEDIAFIRE",
            page_url=page_url,
            download_url=page_url,
            file_name="XIUREN_No.3684.rar",
            remote_folder="llpowfz10s7hep8",
            metadata={},
        )
        refreshed = ParsedSource(
            site="MEDIAFIRE",
            page_url=page_url,
            download_url=direct_url,
            file_name="XIUREN_No.3684.rar",
            remote_folder="llpowfz10s7hep8",
            metadata={"resource_id": "llpowfz10s7hep8", "resolved_live": True},
        )
        result = DownloadResult(
            site="MEDIAFIRE",
            source_url=direct_url,
            local_path="/tmp/media-shuttle/tmp.part",
            size_bytes=1069547520,
            file_name="XIUREN_No.3684.rar",
            remote_folder="llpowfz10s7hep8",
        )

        with patch("core.providers.downloaders_sites.mediafire.resolve_mediafire_source", return_value=refreshed), patch(
            "core.providers.downloaders_sites.mediafire.download_live_generic", return_value=result
        ) as mocked_download:
            download = downloader.download(source)

        self.assertEqual(download.source_url, direct_url)
        self.assertEqual(download.file_name, "XIUREN_No.3684.rar")
        self.assertEqual(source.download_url, direct_url)
        mocked_download.assert_called_once()

    def test_mega_parser_should_identify_site(self):
        registry = default_parser_registry()
        page_url = "https://mega.nz/file/JvQU2JpD#TVhaVbyol9z86z_VadLmSQr6zaOV8wxjFW2NuSP1uKY"
        sources = registry.parse(page_url)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "MEGA")
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "mega_JvQU2JpD.bin")
        self.assertEqual(sources[0].remote_folder, "JvQU2JpD")

    def test_mega_live_parser_should_not_resolve_download_link(self):
        registry = default_parser_registry(mode="live")
        page_url = "https://mega.nz/file/JvQU2JpD#TVhaVbyol9z86z_VadLmSQr6zaOV8wxjFW2NuSP1uKY"

        with patch("core.providers.parsers_sites.mega._mega_request_public_download") as mocked_request:
            sources = registry.parse(page_url)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "MEGA")
        self.assertEqual(sources[0].page_url, page_url)
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "mega_JvQU2JpD.bin")
        self.assertEqual(sources[0].remote_folder, "JvQU2JpD")
        self.assertFalse(sources[0].metadata["resolved_live"])
        mocked_request.assert_not_called()

    def test_mega_live_downloader_should_refresh_direct_url(self):
        downloader = default_downloader_registry(mode="live")
        page_url = "https://mega.nz/file/JvQU2JpD#TVhaVbyol9z86z_VadLmSQr6zaOV8wxjFW2NuSP1uKY"
        direct_url = "http://gfs206n196.userstorage.mega.co.nz/dl/example-token"
        source = ParsedSource(
            site="MEGA",
            page_url=page_url,
            download_url=page_url,
            file_name="mega_JvQU2JpD.bin",
            remote_folder="JvQU2JpD",
            metadata={},
        )
        refreshed = ParsedSource(
            site="MEGA",
            page_url=page_url,
            download_url=direct_url,
            file_name="Hungarian_1st.mp4",
            remote_folder="JvQU2JpD",
            metadata={"resource_id": "JvQU2JpD", "resolved_live": True},
        )
        result = DownloadResult(
            site="MEGA",
            source_url=direct_url,
            local_path="/tmp/media-shuttle/tmp.part",
            size_bytes=181939593,
            file_name="Hungarian_1st.mp4",
            remote_folder="JvQU2JpD",
        )

        with patch("core.providers.downloaders_sites.mega.resolve_mega_source", return_value=refreshed), patch(
            "core.providers.downloaders_sites.mega.download_live_generic", return_value=result
        ) as mocked_download:
            download = downloader.download(source)

        self.assertEqual(download.source_url, direct_url)
        self.assertEqual(download.file_name, "Hungarian_1st.mp4")
        self.assertEqual(source.download_url, direct_url)
        self.assertEqual(source.file_name, "Hungarian_1st.mp4")
        mocked_download.assert_called_once()

    def test_transfer_parser_should_identify_site(self):
        registry = default_parser_registry()
        page_url = "https://transfer.it/t/tWvdyDpHkRCZ"
        sources = registry.parse(page_url)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "TRANSFERIT")
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "transfer_tWvdyDpHkRCZ.bin")
        self.assertEqual(sources[0].remote_folder, "tWvdyDpHkRCZ")

    def test_transfer_live_downloader_should_refresh_direct_url(self):
        downloader = default_downloader_registry(mode="live")
        page_url = "https://transfer.it/t/tWvdyDpHkRCZ"
        direct_url = "https://gfs270n389.userstorage.mega.co.nz/dl/example-token"
        source = ParsedSource(
            site="TRANSFERIT",
            page_url=page_url,
            download_url=page_url,
            file_name="832416793426411520.mp4",
            remote_folder="tWvdyDpHkRCZ",
            metadata={"share_id": "tWvdyDpHkRCZ", "node_handle": "KKw1zT5Z"},
        )
        refreshed = ParsedSource(
            site="TRANSFERIT",
            page_url=page_url,
            download_url=direct_url,
            file_name="832416793426411520.mp4",
            remote_folder="tWvdyDpHkRCZ",
            metadata={"share_id": "tWvdyDpHkRCZ", "node_handle": "KKw1zT5Z", "resolved_live": True},
        )
        result = DownloadResult(
            site="TRANSFERIT",
            source_url=direct_url,
            local_path="/tmp/media-shuttle/tmp.part",
            size_bytes=2001281384,
            file_name="832416793426411520.mp4",
            remote_folder="tWvdyDpHkRCZ",
        )

        with patch("core.providers.downloaders_sites.transfer.resolve_transfer_source", return_value=refreshed), patch(
            "core.providers.downloaders_sites.transfer.download_live_generic", return_value=result
        ) as mocked_download:
            download = downloader.download(source)

        self.assertEqual(download.source_url, direct_url)
        self.assertEqual(download.file_name, "832416793426411520.mp4")
        self.assertEqual(source.download_url, direct_url)
        self.assertEqual(source.file_name, "832416793426411520.mp4")
        mocked_download.assert_called_once()

    def test_filester_parser_should_identify_site(self):
        registry = default_parser_registry()
        page_url = "https://filester.me/d/QHdR2xo"
        sources = registry.parse(page_url)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "FILESTER")
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "filester_QHdR2xo.bin")
        self.assertEqual(sources[0].remote_folder, "QHdR2xo")

    def test_filester_live_downloader_should_refresh_direct_url(self):
        downloader = default_downloader_registry(mode="live")
        page_url = "https://filester.me/d/QHdR2xo"
        direct_url = (
            "https://cache1.filester.me/d/"
            "36633065323137652d336439352d346163662d386433332d363463366239386638376565"
            "?download=true"
        )
        source = ParsedSource(
            site="FILESTER",
            page_url=page_url,
            download_url=page_url,
            file_name="832416793426411520.mp4",
            remote_folder="6c0e217e-3d95-4acf-8d33-64c6b98f87ee",
            metadata={"file_slug": "QHdR2xo", "file_uuid": "6c0e217e-3d95-4acf-8d33-64c6b98f87ee"},
        )
        refreshed = ParsedSource(
            site="FILESTER",
            page_url=page_url,
            download_url=direct_url,
            file_name="832416793426411520.mp4",
            remote_folder="6c0e217e-3d95-4acf-8d33-64c6b98f87ee",
            metadata={
                "file_slug": "QHdR2xo",
                "file_uuid": "6c0e217e-3d95-4acf-8d33-64c6b98f87ee",
                "resolved_live": True,
            },
        )
        result = DownloadResult(
            site="FILESTER",
            source_url=direct_url,
            local_path="/tmp/media-shuttle/tmp.part",
            size_bytes=2001281384,
            file_name="832416793426411520.mp4",
            remote_folder="6c0e217e-3d95-4acf-8d33-64c6b98f87ee",
        )

        with patch("core.providers.downloaders_sites.filester.resolve_filester_source", return_value=refreshed), patch(
            "core.providers.downloaders_sites.filester.download_live_generic", return_value=result
        ) as mocked_download:
            download = downloader.download(source)

        self.assertEqual(download.source_url, direct_url)
        self.assertEqual(download.file_name, "832416793426411520.mp4")
        self.assertEqual(source.download_url, direct_url)
        self.assertEqual(source.file_name, "832416793426411520.mp4")
        mocked_download.assert_called_once()

    def test_turbo_parser_should_identify_site(self):
        registry = default_parser_registry()
        page_url = "https://turbo.cr/v/gLlVkUMFwaO"
        sources = registry.parse(page_url)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "TURBO")
        self.assertEqual(sources[0].download_url, page_url)
        self.assertEqual(sources[0].file_name, "turbo_gLlVkUMFwaO.bin")
        self.assertEqual(sources[0].remote_folder, "gLlVkUMFwaO")

    def test_turbo_live_downloader_should_refresh_direct_url(self):
        downloader = default_downloader_registry(mode="live")
        page_url = "https://turbo.cr/v/gLlVkUMFwaO"
        direct_url = (
            "https://dl5.turbocdn.st/data/gLlVkUMFwaO.mp4"
            "?exp=1773592839&token=abc123&fn=gLlVkUMFwaO.mp4"
        )
        source = ParsedSource(
            site="TURBO",
            page_url=page_url,
            download_url=page_url,
            file_name="gLlVkUMFwaO.mp4",
            remote_folder="gLlVkUMFwaO",
            metadata={"slug": "gLlVkUMFwaO"},
        )
        refreshed = ParsedSource(
            site="TURBO",
            page_url=page_url,
            download_url=direct_url,
            file_name="gLlVkUMFwaO.mp4",
            remote_folder="gLlVkUMFwaO",
            metadata={"slug": "gLlVkUMFwaO", "resolved_live": True},
        )
        result = DownloadResult(
            site="TURBO",
            source_url=direct_url,
            local_path="/tmp/media-shuttle/tmp.part",
            size_bytes=176454656,
            file_name="gLlVkUMFwaO.mp4",
            remote_folder="gLlVkUMFwaO",
        )

        with patch("core.providers.downloaders_sites.turbo.resolve_turbo_source", return_value=refreshed), patch(
            "core.providers.downloaders_sites.turbo.download_live_generic", return_value=result
        ) as mocked_download:
            download = downloader.download(source)

        self.assertEqual(download.source_url, direct_url)
        self.assertEqual(download.file_name, "gLlVkUMFwaO.mp4")
        self.assertEqual(source.download_url, direct_url)
        self.assertEqual(source.file_name, "gLlVkUMFwaO.mp4")
        mocked_download.assert_called_once()

    def test_additional_site_parsers_should_identify_site(self):
        registry = default_parser_registry()
        cases = [
            ("https://cyberdrop.me/f/abc123", "CYBERDROP"),
            ("https://cyberfile.me/abc123", "CYBERFILE"),
            ("https://filester.me/d/QHdR2xo", "FILESTER"),
            ("https://turbo.cr/v/gLlVkUMFwaO", "TURBO"),
            ("https://pixeldrain.com/u/xyz987", "PIXELDRAIN"),
            ("https://drive.google.com/file/d/1abcDEF123456789/view", "GD"),
            ("https://mega.nz/file/abc#key", "MEGA"),
            ("https://saint.to/embed/abc123", "SAINT"),
            ("https://transfer.it/t/tWvdyDpHkRCZ", "TRANSFERIT"),
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
            "https://filester.me/d/QHdR2xo",
            "https://turbo.cr/v/gLlVkUMFwaO",
            "https://pixeldrain.com/u/xyz987",
            "https://drive.google.com/file/d/1abcDEF123456789/view",
            "https://mega.nz/file/abc#key",
            "https://saint.to/embed/abc123",
            "https://transfer.it/t/tWvdyDpHkRCZ",
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
