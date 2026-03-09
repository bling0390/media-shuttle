from __future__ import annotations

import unittest

from core.providers.parsers_sites.pixeldrain import (
    _pixeldrain_extract_initial_node,
    _pixeldrain_sources_from_filesystem_page,
    _pixeldrain_sources_from_list,
)


class PixeldrainParserTests(unittest.TestCase):
    def test_filesystem_share_expands_to_direct_file_links(self) -> None:
        html = """
        <script>
        window.initial_node = {"path":[{"type":"dir","path":"/TLwB6XRG","name":"15.撮影者S August 2025","id":"TLwB6XRG"}],"base_index":0,"children":[{"type":"file","path":"/TLwB6XRG/.search_index.gz","name":".search_index.gz"},{"type":"file","path":"/TLwB6XRG/video one.mp4","name":"video one.mp4"},{"type":"file","path":"/TLwB6XRG/nested/video two.mp4","name":"video two.mp4"}],"permissions":{"read":true}};
        window.user = {};
        </script>
        """

        node = _pixeldrain_extract_initial_node(html)
        self.assertEqual(node["path"][0]["name"], "15.撮影者S August 2025")

        sources = _pixeldrain_sources_from_filesystem_page("https://pixeldrain.com/d/TLwB6XRG", "TLwB6XRG", html)
        self.assertEqual(len(sources), 2)
        self.assertEqual(
            sources[0].download_url,
            "https://pixeldrain.com/api/filesystem/TLwB6XRG/video%20one.mp4",
        )
        self.assertEqual(
            sources[1].download_url,
            "https://pixeldrain.com/api/filesystem/TLwB6XRG/nested/video%20two.mp4",
        )
        self.assertEqual(sources[0].remote_folder, "15.撮影者S August 2025")

    def test_list_share_expands_to_direct_file_links(self) -> None:
        payload = {
            "success": True,
            "id": "abc123",
            "title": "Sample List",
            "files": [
                {"id": "_SqVWi", "name": "01 intro.mp4"},
                {"id": "RKwgZb", "name": "02 outro.mp4"},
            ],
        }

        sources = _pixeldrain_sources_from_list("https://pixeldrain.com/l/abc123", "abc123", payload)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].download_url, "https://pixeldrain.com/api/file/_SqVWi")
        self.assertEqual(sources[1].download_url, "https://pixeldrain.com/api/file/RKwgZb")
        self.assertEqual(sources[0].remote_folder, "Sample List")


if __name__ == "__main__":
    unittest.main()
