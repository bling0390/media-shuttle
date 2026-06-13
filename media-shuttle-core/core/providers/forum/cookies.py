"""Cookie env parsing for forum extractors.

The single source of truth for "which cookie header do I send to this
forum host?" lives here. New forum sites are wired up by adding an
``FORUMS_<NAME>_COOKIE`` env var (semicolon-separated ``k=v`` pairs)
and registering a hostname -> env-var name mapping in
:func:`build_cookie_header_resolver`. Everything else (HTTP client,
extractor, rate limiting) is host-agnostic.

Why the env var is "the raw Cookie header value" instead of a JSON
blob: keeping the wire format identical to what the browser sends
makes it trivial to copy a freshly-captured cookie out of devtools
into the env, with no escaping. The few cookies we drop (UA / GA /
DuckDuckGo privacy extension markers) are filtered at request time
inside :func:`resolve_cookie_header` so an operator that does paste
the full browser cookie blob in does not get mysterious 403s.
"""

from __future__ import annotations

import os
import re
from typing import Callable
from urllib.parse import urlparse

# Names of the cookies we *keep* from the env. Anything else is
# dropped at request time. ``__ddg*`` (DuckDuckGo privacy essentials)
# and ``_ga*`` (Google Analytics) have no value to a server-side
# fetch and may even trigger anti-bot heuristics.
#
# SocialMediaGirls uses the default XenForo cookie names
# (``xf_session`` / ``xf_user`` / ``xf_csrf``). Other XenForo
# installations prefix the same three cookies with a per-site
# token (``yMziCv8BrCZz1o7_session`` on SimpCity, etc.). To
# keep the resolver agnostic of the prefix we accept any cookie
# whose name *ends* with ``_session`` / ``_user`` / ``_csrf``;
# the prefix is opaque to the server and we never inspect it.
_ALLOWED_COOKIE_NAMES = frozenset(
    {
        "xf_session",
        "xf_user",
        "xf_csrf",
    }
)
_ALLOWED_COOKIE_SUFFIXES = ("_session", "_user", "_csrf")


def _cookie_name_allowed(name: str) -> bool:
    """Return True if ``name`` should be forwarded to the forum.

    Exact-match for the canonical XenForo names (SocialMediaGirls
    on the default install) and suffix-match for prefixed
    variants (SimpCity, custom XenForo deployments, etc.).
    Anything else is dropped — see :data:`_ALLOWED_COOKIE_NAMES`
    for the rationale (anti-bot heuristics on ``__ddg*`` / ``_ga*``).
    """
    if name in _ALLOWED_COOKIE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in _ALLOWED_COOKIE_SUFFIXES)

# Hostname suffixes that we recognise. The match is suffix-based so
# ``forums.socialmediagirls.com`` matches the entry registered for
# ``socialmediagirls.com``. New sites are added by appending to
# :data:`_HOST_TO_ENV_VAR` in :func:`build_cookie_header_resolver`.
_ENV_VAR_PREFIX = "FORUMS_"
_ENV_VAR_SUFFIX = "_COOKIE"


def _env_var_name(forum_key: str) -> str:
    """Translate ``"socialmediagirls"`` -> ``"FORUMS_SOCIALMEDIAGIRLS_COOKIE"``.

    The env var name is uppercased and stripped of non-alphanumerics
    to keep the lookup predictable regardless of how the operator
    types the forum key in config files.
    """
    key = re.sub(r"[^A-Za-z0-9]+", "_", forum_key).strip("_").upper()
    return f"{_ENV_VAR_PREFIX}{key}{_ENV_VAR_SUFFIX}"


def _parse_cookie_kv(blob: str) -> dict[str, str]:
    """Parse ``"k1=v1; k2=v2"`` into a dict.

    No base64 / JSON / special-case quoting. Tolerant of stray spaces.
    Empty pairs and pairs without ``=`` are silently dropped. We do
    *not* enforce uniqueness — if the same key appears twice the
    last one wins, which is the same semantics the browser uses
    when serializing cookies to a request.
    """
    out: dict[str, str] = {}
    for raw in (blob or "").split(";"):
        item = raw.strip()
        if not item or "=" not in item:
            continue
        name, _, value = item.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        out[name] = value
    return out


def _filter_allowed(cookies: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in cookies.items() if _cookie_name_allowed(k)}


def _cookie_header_for(cookies: dict[str, str]) -> str:
    """Render a dict back to a ``Cookie:`` header value.

    Keys are kept as-is (XenForo names are already case-sensitive
    and lowercased by us in the env). ``; `` separator matches what
    browsers and httpx send.
    """
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def build_cookie_header_resolver(
    host_to_forum_key: dict[str, str],
) -> Callable[[str], str | None]:
    """Build a resolver: ``(url) -> "Cookie: ..." | None``.

    The resolver looks up the URL's hostname in ``host_to_forum_key``
    (suffix match against each registered host), reads the
    corresponding ``FORUMS_<KEY>_COOKIE`` env var at *call time*
    (so a worker re-reads the env after a redeploy without
    restarting), filters to allowed names, and renders the
    remaining cookies as a single ``Cookie`` header.

    Returns ``None`` when no forum key matches the URL. Callers
    should treat that as "send the request unauthenticated" and
    surface a 401/302-to-login as a hard error to the user.

    The env var is re-read on every call deliberately: it lets an
    operator ``docker exec`` and edit the env without bouncing
    the worker. The cost is one ``os.environ`` dict lookup per
    HTTP request, which is negligible.
    """
    # Normalize registered hosts to lowercase for suffix matching.
    entries: list[tuple[str, str]] = [
        (host.lower(), _env_var_name(forum_key))
        for host, forum_key in host_to_forum_key.items()
    ]

    def _resolve(url: str) -> str | None:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return None
        for registered_host, env_name in entries:
            if host == registered_host or host.endswith("." + registered_host):
                raw = os.environ.get(env_name, "").strip()
                if not raw:
                    return None
                filtered = _filter_allowed(_parse_cookie_kv(raw))
                if not filtered:
                    return None
                return _cookie_header_for(filtered)
        return None

    return _resolve


# Default mapping for v1: SocialMediaGirls. simpcity will be added
# here later without touching anything else in the codebase.
DEFAULT_HOST_TO_FORUM_KEY: dict[str, str] = {
    "socialmediagirls.com": "socialmediagirls",
    "forums.socialmediagirls.com": "socialmediagirls",
    "simpcity.cr": "simpcity",
}
