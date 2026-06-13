"""SocialMediaGirls (SMG) thread extractor.

SMG is a stock XenForo v2 install with the default theme, so the
generic :class:`XenForoThreadParser` does all the heavy lifting.
The only reason this module exists is to declare the ``forum_key``
constant that ties into the env-var lookup
(``FORUMS_SOCIALMEDIAGIRLS_COOKIE``).

If SMG ever rolls a custom theme that changes the ``.bbWrapper``
selector or hides ``.pageNav`` behind a custom widget, the change
belongs here, not in :mod:`xenforo`. The base class stays clean
for the next XenForo forum to inherit.
"""

from __future__ import annotations

from .xenforo import XenForoThreadParser


class SocialMediaGirlsExtractor(XenForoThreadParser):
    forum_key: str = "socialmediagirls"
