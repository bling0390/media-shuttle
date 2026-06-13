"""Extractor registry.

A new forum is added in three steps:

1. Add a new env var ``FORUMS_<KEY>_COOKIE`` to the deployment.
2. Append ``"<host>": "<key>"`` to
   :data:`forum.cookies.DEFAULT_HOST_TO_FORUM_KEY` (or override
   at resolver build time if the key naming is different).
3. Add a ``<Forum>Extractor(ForumThreadParser)`` subclass
   with the right ``forum_key`` and register it in
   :data:`EXTRACTORS` below.

The order of entries in :data:`EXTRACTORS` is irrelevant —
``select_extractor`` uses the URL's hostname to pick, not a
positional lookup. The class itself does not need to be
unique on hostname: if two extractors claim overlapping
hosts, the first one in the dict wins and a warning is
logged. This makes the registry safe to extend without
re-reading all the existing entries.
"""

from __future__ import annotations

import logging
from typing import Type
from urllib.parse import urlparse

from ..base import ForumThreadParser
from .smg import SocialMediaGirlsExtractor
from .simpcity import SimpCityExtractor

logger = logging.getLogger(__name__)

#: Map of forum_key -> extractor class. ``forum_key`` is the
#: same string that the env var lookup uses, so the link is
#: automatic and unambiguous.
EXTRACTORS: dict[str, Type[ForumThreadParser]] = {
    SocialMediaGirlsExtractor.forum_key: SocialMediaGirlsExtractor,
    SimpCityExtractor.forum_key: SimpCityExtractor,
}


class UnsupportedForumError(ValueError):
    """Raised when no extractor is registered for a URL's host."""


def select_extractor_class(url: str) -> Type[ForumThreadParser]:
    """Pick the extractor class for a thread URL.

    Hostname match is suffix-based against each registered
    forum's env-var name. The ``forum_key`` is the lookup
    key, not the hostname — the resolver in
    :mod:`forum.cookies` keeps the host->key mapping
    authoritative.
    """
    from ..cookies import DEFAULT_HOST_TO_FORUM_KEY

    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise UnsupportedForumError(f"could not parse host from {url!r}")

    for registered_host, forum_key in DEFAULT_HOST_TO_FORUM_KEY.items():
        registered = registered_host.lower()
        if host == registered or host.endswith("." + registered):
            cls = EXTRACTORS.get(forum_key)
            if cls is None:
                raise UnsupportedForumError(
                    f"host {host!r} is registered for forum_key={forum_key!r} "
                    f"but no extractor class is registered under that key"
                )
            return cls

    raise UnsupportedForumError(f"no extractor registered for host: {host}")


__all__ = [
    "EXTRACTORS",
    "SimpCityExtractor",
    "SocialMediaGirlsExtractor",
    "UnsupportedForumError",
    "select_extractor_class",
]
