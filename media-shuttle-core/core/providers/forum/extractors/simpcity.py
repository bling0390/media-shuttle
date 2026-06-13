"""SimpCity thread extractor.

SimpCity is a stock XenForo v2 install behind a Cloudflare-style
DDoS-Guard challenge, but the actual thread pages render with
the same ``.bbWrapper`` / ``.pageNav-main`` structure as any
other XenForo forum, so the generic
:class:`XenForoThreadParser` does all the heavy lifting. The
only reason this module exists is to declare the
``forum_key`` constant that ties into the env-var lookup
(``FORUMS_SIMPCITY_COOKIE``).

The cookie naming is the one quirk: the per-site XenForo
install prefixes the canonical ``_session`` / ``_user`` /
``_csrf`` cookies with a random token (e.g.
``yMziCv8BrCZz1o7_session``). The cookie env-var loader
accepts any name ending in those suffixes, so we do not
have to special-case the prefix here — but we *do* need a
distinct ``forum_key`` so the resolver picks the right
env var (``FORUMS_SIMPCITY_COOKIE``) and the worker logs
can distinguish the two forums.
"""

from __future__ import annotations

from .xenforo import XenForoThreadParser


class SimpCityExtractor(XenForoThreadParser):
    forum_key: str = "simpcity"
