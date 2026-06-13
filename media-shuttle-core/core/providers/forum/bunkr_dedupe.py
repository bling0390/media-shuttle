"""Bunkr multi-domain dedupe.

Bunkr rotates its primary domain (``bunkr.si`` -> ``.la`` -> ``.su``
-> ``.cr`` -> ``.so`` -> ``.sx`` -> ``.cv`` -> ...). The *content*
on each domain is shared, so the same file uploaded once will
appear in forum threads as N copies pointing at N different
domains. Downloading all N is wasted bandwidth and quota.

Dedup strategy: the *path* part of a bunkr URL uniquely identifies
its content. So we key by path (case-insensitive, trailing-slash
stripped) and keep only the first occurrence in input order.
The order preservation matters because forum threads list the
newest reply first when iterated in reverse — keeping the first
URL we see for a path preserves the operator's likely "I want
the latest mirror" intent.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Rotating set, current as of 2025. We deliberately use a suffix
# regex (not equality) so any bunkr.<tld> variant is caught even
# if we missed a TLD. The alternation is explicit (vs. a generic
# "bunkr.<anything>") so that bunkr.su / bunkr.si / bunkr.la etc.
# are caught but bunkr-fan-clone.example (random third-party) is
# not.
_BUNKR_HOST_RE = re.compile(
    r"\b(bunkr\.(?:si|la|su|cr|so|sx|cv|to|ac|lol|foo|li|me|icu))\b",
    re.IGNORECASE,
)


def _is_bunkr_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host == "bunkr.si" or host.endswith(".bunkr.si"):
        return True
    return bool(_BUNKR_HOST_RE.search(host))


def _path_key(url: str) -> str:
    """Normalize a bunkr URL to a path-only dedupe key.

    Examples (all -> same key):
        https://bunkr.si/v/abc123  ->  /v/abc123
        bunkr.la/v/abc123/        ->  /v/abc123
        HTTP://BUNKR.SU/v/ABC123   ->  /v/abc123
    """
    path = (urlparse(url).path or "").strip()
    if not path:
        return ""
    # Strip a single trailing slash so ``/v/abc123/`` and ``/v/abc123``
    # collapse. We don't strip embedded slashes — those carry
    # content identity (e.g. ``/a/<album>/<file>``).
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path.lower()


def dedupe_bunkr_mirrors(urls: list[str]) -> list[str]:
    """Deduplicate bunkr URLs that point at the same content.

    Non-bunkr URLs pass through unchanged and are *not* deduped —
    that's a different problem (exact-URL dedupe is handled
    elsewhere in the extractor).
    """
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if _is_bunkr_host(url):
            key = _path_key(url)
            if not key:
                # Bunkr URL with no path is malformed; keep it
                # anyway so the parser surfaces a real error
                # rather than us silently dropping it.
                out.append(url)
                continue
            if key in seen:
                continue
            seen.add(key)
        out.append(url)
    return out
