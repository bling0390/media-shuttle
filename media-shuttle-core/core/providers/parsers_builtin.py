from __future__ import annotations

from .parsers_sites import (
    is_bunkr,
    is_bunkr_album,
    is_coomer,
    is_cyberdrop,
    is_cyberdrop_album,
    is_cyberfile,
    is_direct_file,
    is_gd,
    is_gofile,
    is_mediafire,
    is_mega,
    is_pixeldrain,
    is_saint,
    is_ytdl,
    parse_bunkr,
    parse_bunkr_album_live,
    parse_bunkr_live,
    parse_coomer,
    parse_cyberdrop,
    parse_cyberdrop_album_live,
    parse_cyberfile,
    parse_gd,
    parse_generic,
    parse_gofile,
    parse_gofile_live,
    parse_mediafire,
    parse_mega,
    parse_pixeldrain,
    parse_saint,
    parse_ytdl,
)
from .types import ParseProvider


def builtin_parse_providers(mode: str) -> list[ParseProvider]:
    providers: list[ParseProvider] = []

    if mode == "live":
        providers.extend(
            [
                ParseProvider("gofile_live", "live", is_gofile, parse_gofile_live),
                ParseProvider("bunkr_album_live", "live", is_bunkr_album, parse_bunkr_album_live),
                ParseProvider("bunkr_live", "live", is_bunkr, parse_bunkr_live),
                ParseProvider("cyberdrop_album_live", "live", is_cyberdrop_album, parse_cyberdrop_album_live),
            ]
        )

    providers.extend(
        [
            ParseProvider("gofile", "mock", is_gofile, parse_gofile),
            ParseProvider("bunkr", "mock", is_bunkr, parse_bunkr),
            ParseProvider("cyberdrop", "mock", is_cyberdrop, parse_cyberdrop),
            ParseProvider("cyberfile", "mock", is_cyberfile, parse_cyberfile),
            ParseProvider("pixeldrain", "mock", is_pixeldrain, parse_pixeldrain),
            ParseProvider("gd", "mock", is_gd, parse_gd),
            ParseProvider("mega", "mock", is_mega, parse_mega),
            ParseProvider("saint", "mock", is_saint, parse_saint),
            ParseProvider("coomer", "mock", is_coomer, parse_coomer),
            ParseProvider("mediafire", "mock", is_mediafire, parse_mediafire),
            ParseProvider("ytdl", "mock", is_ytdl, parse_ytdl),
            ParseProvider("direct_file", "mock", is_direct_file, parse_generic),
            ParseProvider("generic_fallback", "all", lambda _url: True, parse_generic),
        ]
    )
    return providers
