"""SimpCity thread extractor.

Stub. SimpCity also runs XenForo, so the same base class is
correct, but the env-var key is different and we want a separate
``forum_key`` so the cookie resolver and worker logs can
distinguish the two forums.

No HTTP behavior is implemented here until we wire up a real
SimpCity cookie and a smoke test. Until then, registering this
class in :mod:`forum.extractors` would only let an operator
hit a clean "cookie env var missing" error — useful for proving
the registration pipeline works end-to-end.
"""

from __future__ import annotations

from .xenforo import XenForoThreadParser


class SimpCityExtractor(XenForoThreadParser):
    forum_key: str = "simpcity"
