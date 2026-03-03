from __future__ import annotations

from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import guess_filename_from_path, is_direct_file_url


def is_direct_file(url: str) -> bool:
    return is_direct_file_url(url)


def parse_generic(url: str) -> list[ParsedSource]:
    name = guess_filename_from_path(url, fallback="download.bin")
    return [
        ParsedSource(
            site=SourceSite.GENERIC.value,
            page_url=url,
            download_url=url,
            file_name=name,
            remote_folder=urlparse(url).netloc or None,
        )
    ]
