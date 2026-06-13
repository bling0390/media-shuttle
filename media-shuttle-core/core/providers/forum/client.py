"""Threaded HTTP client for forum pages.

Two responsibilities:

1. Pacing. Forum sites aggressively throttle / 403 users that hit
   them at a constant high rate. We randomize sleep in three tiers
   to mimic a human skimming: per-page (fast), per-burst (medium),
   per-circuit (long, resets any rate-limit token the site tracks).
2. Cookie injection. Every request gets the right ``Cookie``
   header for the URL's host. Cookies are resolved via the env
   resolver at request time, so operators can rotate them without
   worker restarts.

The client is *not* concerned with parsing the page body — it
yields raw HTML to the extractor. The split is deliberate: parsers
change (XenForo 1.x vs 2.x vs custom), pacing does not.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx

from ..user_agents import with_random_user_agent
from . import config


@dataclass
class FetchedPage:
    """One page of forum HTML.

    ``page_number`` is 1-indexed and reflects the position in the
    *original* thread (page 1 is the OP, last page is the newest
    reply). Callers iterating in reverse get the latest content
    first.
    """

    page_number: int
    url: str
    html: str
    status_code: int


class ForumFetchError(RuntimeError):
    """Raised when a forum page could not be retrieved cleanly.

    The caller (extractor) is expected to treat this as a hard
    failure for the *current* task, not silently swallow. The
    already-fanned-out parse_link tasks continue independently.
    """


class ForumClient:
    """Paced, cookie-aware client for forum HTML pages.

    The pacing math is per-instance: each ForumClient counts its
    own pages-fetched so a long-lived worker sharing the client
    across multiple forum tasks naturally accumulates the
    "circuit" cooldown every 50 pages regardless of which task
    triggered them. This is intentional — a single operator
    who runs 5 forum tasks in quick succession should not be
    able to dodge the 50-page cooldown by switching tasks.

    ``cookie_resolver`` is whatever
    :func:`forum.cookies.build_cookie_header_resolver` returns.
    """

    def __init__(
        self,
        cookie_resolver: Callable[[str], str | None],
        timeout_seconds: float = 45.0,
    ) -> None:
        self._resolver = cookie_resolver
        self._timeout = timeout_seconds
        self._pages_fetched = 0
        # ``_last_status`` exposes the HTTP status of the most
        # recent request to callers that want to log / alert on
        # a 401/403/429 (e.g. "cookie expired" detection). It is
        # not part of the public contract for the success path.
        self._last_status: int | None = None

    @property
    def pages_fetched(self) -> int:
        return self._pages_fetched

    @property
    def last_status(self) -> int | None:
        return self._last_status

    def reset_circuit(self) -> None:
        """Reset the cumulative page counter.

        Useful in tests and in worker fork boundaries (so a
        fresh worker process starts the 50-page cooldown timer
        from zero). Production code rarely calls this.
        """
        self._pages_fetched = 0

    def fetch_page(self, url: str) -> FetchedPage:
        """Fetch a single page, applying pacing and the cookie header.

        Raises :class:`ForumFetchError` on any non-2xx response or
        transport error. Callers are expected to surface a clean
        "forum fetch failed" message to the user; retry logic is
        not built in (per Q2: forum task failure is non-retryable).
        """
        headers = with_random_user_agent(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )
        cookie = self._resolver(url)
        if cookie:
            headers["Cookie"] = cookie

        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ForumFetchError(f"transport error fetching {url}: {exc}") from exc

        self._last_status = response.status_code
        if response.status_code >= 400:
            raise ForumFetchError(
                f"http {response.status_code} fetching {url}"
            )
        html = response.text or ""
        if not html:
            raise ForumFetchError(f"empty body fetching {url}")

        return FetchedPage(
            page_number=0,  # filled in by the caller / iterator
            url=url,
            html=html,
            status_code=response.status_code,
        )

    def _sleep(self, low: float, high: float) -> None:
        if high <= low:
            time.sleep(max(0.0, low))
            return
        time.sleep(random.uniform(low, high))

    def _pace(self) -> None:
        """Apply the three-tier sleep schedule.

        Order matters: we always do the base sleep, then the
        burst sleep if the counter is a multiple of the burst
        interval, then the circuit sleep if a multiple of the
        circuit interval. The two higher-tier sleeps stack on
        the same page, which is what we want: a page that
        happens to be the 50th fetch gets both a burst sleep
        (8-15s) and a circuit sleep (30-60s) before the request
        goes out.
        """
        base = config.base_sleep_range()
        self._sleep(base[0], base[1])

        if self._pages_fetched and self._pages_fetched % config.burst_interval() == 0:
            burst = config.burst_sleep_range()
            self._sleep(burst[0], burst[1])

        if self._pages_fetched and self._pages_fetched % config.circuit_interval() == 0:
            circuit = config.circuit_sleep_range()
            self._sleep(circuit[0], circuit[1])

    def iter_pages_reverse(
        self,
        page_urls: list[tuple[int, str]],
    ) -> Iterator[FetchedPage]:
        """Iterate ``(page_number, url)`` pairs newest-first.

        ``page_urls`` is the full ordered list (page 1 first, last
        page last) of URLs to visit. The iterator yields them in
        reverse order, applying pacing *between* requests. The
        very first request has no sleep before it (the iterator
        has not yet "done" anything).

        On error, the iteration stops and the exception
        propagates. Already-yielded pages are considered
        consumed.
        """
        for page_number, url in reversed(page_urls):
            self._pace()
            page = self.fetch_page(url)
            page.page_number = page_number
            self._pages_fetched += 1
            yield page
