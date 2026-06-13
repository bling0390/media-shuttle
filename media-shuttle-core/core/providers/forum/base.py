"""Abstract base for forum-thread extractors.

A "forum thread" is, for our purposes, a multi-page web page
listing replies, where each reply may contain download links.
The base class captures the *common* flow — fetch pages, extract
links, dedupe, fan out — and leaves the *forum-specific* bits
to subclasses: total-page count detection, post-content selector,
and any site-specific URL filtering.

Subclass contract (minimal):

* :meth:`detect_last_page` — given the HTML of page 1, return
  the integer 1-indexed page number of the newest page. If the
  thread is single-page, return 1.
* :meth:`extract_post_links` — given the HTML of one page, return
  the list of raw ``<a href>`` values found inside post bodies.
  No filtering, no deduping — that is the base class's job.

Everything else (cookie resolution, pacing, bunkr dedupe, link
filtering against the existing parser matchers, fan-out cap)
is centralized here so adding a new forum is ~30 lines of code
in a new ``extractors/<forum>.py`` file.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Iterable

from .bunkr_dedupe import dedupe_bunkr_mirrors
from .client import FetchedPage, ForumClient


@dataclass
class ExtractedThread:
    """Result of walking a forum thread.

    ``post_links`` is the post-filtered, post-deduped list of
    candidate download links. The forum task will then
    intersect this with the parser matchers (gofile / bunkr /
    ...) and only fan out matches.
    """

    forum_key: str
    last_page: int
    pages_walked: int
    post_links: list[str]


# All known parser matcher functions, imported lazily to keep
# import-time cost low (the forum module is also used by the
# api image, which doesn't need lxml / bunkr).
def _all_parser_matchers() -> list:
    """Return every ``is_<site>`` matcher from the parsers builtin,
    *excluding* the ``generic_fallback`` matcher (which accepts
    every URL and would defeat the point of filtering).

    These are the same matchers the pipeline uses to decide
    which parser to dispatch to. Reusing them here means a
    link in a forum post is fan-out eligible if and only if
    the existing pipeline already knows how to download it
    as a *known* source. A random ``https://example.com/x``
    link from a forum signature would otherwise be
    fan-out-ed and immediately fail in the parser, wasting
    a celery slot and a mongo record.

    No new download logic is required for the forum dispatcher.
    """
    from ..parsers_builtin import builtin_parse_providers

    providers = builtin_parse_providers("live")
    out: list = []
    for provider in providers:
        # The ``generic_fallback`` matcher is a sentinel that
        # accepts every URL — see builtin_parse_providers.
        # Skip it here so unknown forum links don't sneak
        # through.
        if provider.name == "generic_fallback":
            continue
        out.append(provider.matcher)
    return out


def filter_supported_links(
    raw_links: Iterable[str],
    matchers: list | None = None,
) -> list[str]:
    """Keep only links that at least one known parser matcher accepts.

    This is the bridge between the forum layer and the existing
    download pipeline: a link the pipeline cannot parse as a
    *known* source is useless to fan out, so we drop it here.
    The order is preserved so the operator can still see
    "we found these 50 gofile links, in this order".
    """
    matchers = matchers if matchers is not None else _all_parser_matchers()
    seen: set[str] = set()
    out: list[str] = []
    for raw in raw_links:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            continue
        if cleaned in seen:
            continue
        if not any(matcher(cleaned) for matcher in matchers):
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


class ForumThreadParser(abc.ABC):
    """Base class for site-specific thread extractors.

    Subclasses must implement :meth:`detect_last_page` and
    :meth:`extract_post_links`. The base class handles
    iteration, pacing, dedup, and the link-vs-parser-matcher
    intersection.
    """

    #: Short identifier (matches the env-var name without the
    #: ``FORUMS_`` prefix / ``_COOKIE`` suffix).
    forum_key: str = ""

    def __init__(self, client: ForumClient) -> None:
        self._client = client

    @abc.abstractmethod
    def detect_last_page(self, page1_html: str, page1_url: str) -> int:
        """Return the 1-indexed last page number for this thread.

        For a single-page thread, return 1. For a thread where
        the page count is genuinely not detectable (rare; e.g.
        a broken forum), return 1 — the iterator will then walk
        just page 1 and the operator sees zero results.
        """

    @abc.abstractmethod
    def build_page_url(self, thread_base_url: str, page_number: int) -> str:
        """Render ``(thread_base_url, page_number)`` -> absolute URL."""

    @abc.abstractmethod
    def extract_post_links(self, page_html: str) -> list[str]:
        """Return the raw ``<a href>`` values found inside post bodies.

        The base class takes care of filtering, dedup, and
        fan-out — this method should focus purely on "find
        the right DOM nodes".
        """

    # ----- the orchestration below is shared -------------------

    def walk(
        self,
        thread_base_url: str,
        max_pages: int,
    ) -> ExtractedThread:
        """Walk the thread (newest pages first, capped) and return links.

        Steps:
            1. Fetch page 1 to detect the last page number.
            2. Build the list of pages to visit: ``[last - max + 1 .. last]``,
               clamped to ``>= 1``.
            3. Iterate the slice in reverse using the paced client.
            4. Extract post links from every page, dedup bunkr
               mirrors, and intersect with the parser matchers.

        Raises :class:`forum.client.ForumFetchError` on the
        first hard fetch failure. Already-collected links from
        earlier pages are discarded along with the exception
        — the orchestrator above the base class is responsible
        for deciding what to do with partial results.
        """
        # 1. Page 1 fetch (un-paced, so the user gets an error
        # quickly if the cookie is broken).
        page1 = self._client.fetch_page(self.build_page_url(thread_base_url, 1))
        last_page = self.detect_last_page(page1.html, page1.url)
        last_page = max(1, int(last_page))

        # 2. Build the slice of pages we want to visit *in
        # addition to* page 1 (which we already fetched above
        # to learn the last page count). The sliding window
        # is anchored at the last page and is ``max_pages -
        # 1`` long so the total visited page count never
        # exceeds ``max_pages``.
        # Example: last_page=100, max_pages=3
        #   -> visit pages 98, 99, 100 (3 pages total,
        #      with page 1 already fetched -> 4 fetches).
        # Example: last_page=2, max_pages=10
        #   -> visit pages 1, 2 (max_pages clamp; we already
        #      have page 1 so only page 2 is fetched here).
        start = max(2, last_page - max_pages + 2)
        tail_pages = list(range(start, last_page + 1))

        # 3. Reverse-iterate. Pacing is applied *between* requests
        # by the client, so this method has no sleep calls.
        all_raw: list[str] = []
        pages_walked = 1  # page 1 already fetched above
        page1_links = self.extract_post_links(page1.html)
        all_raw.extend(page1_links)

        if tail_pages:
            tail = [
                (n, self.build_page_url(thread_base_url, n)) for n in tail_pages
            ]
            for fetched in self._client.iter_pages_reverse(tail):
                pages_walked += 1
                all_raw.extend(self.extract_post_links(fetched.html))

        # 4. Filter + dedupe. Bunkr dedupe runs *first* because it
        # needs the bunkr-domain variants collapsed before
        # exact-URL dedup, then we intersect with parser matchers.
        after_bunkr = dedupe_bunkr_mirrors(all_raw)
        supported = filter_supported_links(after_bunkr)

        return ExtractedThread(
            forum_key=self.forum_key,
            last_page=last_page,
            pages_walked=pages_walked,
            post_links=supported,
        )
