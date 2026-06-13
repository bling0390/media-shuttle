"""XenForo (v1 and v2) thread extractor.

The page structure we rely on:

* Page navigation lives in ``.pageNav`` and renders ``<a>`` tags
  pointing at ``?page=N`` (or the rewritten ``/page-N`` URL). The
  *last* numeric page is the total.
* Post bodies live in ``<div class="bbWrapper">`` (v2) or
  ``<div class="messageContent">`` (v1). v2 is the common case
  for the forums we target.
* Each post may contain ``<a href="...">`` for download links.
  We pull the ``href`` from any ``<a>`` whose nearest ancestor
  post body is a ``bbWrapper``.

The detection helpers (page number from URL, page count from
``.pageNav``, link extraction) are split out into module-level
functions so ``smg.py`` and ``simpcity.py`` can share them
verbatim. The :class:`XenForoThreadParser` below bakes them into
the abstract base; if a future forum uses XenForo but with a
slightly different layout (e.g. custom theme), subclass and
override the relevant helper.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from lxml import html as lxml_html

from ..base import ForumThreadParser

# ``/page-N`` style (XenForo's "friendly URLs" rewrite) or
# ``?page=N`` (default). Either is matched.
_PAGE_PATH_RE = re.compile(r"/page-(\d+)(?:/|$)", re.IGNORECASE)
_PAGE_QUERY_RE = re.compile(r"(?:^|&)page=(\d+)(?:&|$)", re.IGNORECASE)


def _page_number_from_url(url: str) -> int | None:
    """Return the 1-indexed page number embedded in a thread URL.

    Returns ``None`` if the URL has no page indicator (which
    means page 1 in XenForo's convention).
    """
    path_match = _PAGE_PATH_RE.search(urlparse(url).path)
    if path_match:
        return int(path_match.group(1))
    query = parse_qs(urlparse(url).query)
    raw = query.get("page", [None])[0]
    if raw and raw.isdigit():
        return int(raw)
    return None


def detect_last_page_from_html(page1_html: str) -> int:
    """Walk a XenForo ``.pageNav`` and return the largest page number.

    The navigation is typically a list of ``<a>`` tags with
    text like ``1``, ``2``, ``3``, ``...``, ``238`` plus
    ``<a class="pageNav-jump" href="...goto/last">``. We
    prefer the explicit "jump to last" link when present
    because some themes render only a few page numbers with
    ellipsis and the numeric list would understate the total.
    """
    try:
        tree = lxml_html.fromstring(page1_html)
    except Exception:
        return 1

    # Strategy 1: explicit "last" link.
    # XenForo renders "Next" / "Prev" / "Last" as <a class="pageNav-jump ...">.
    # We must filter to anchors whose text or class is "last" only —
    # "Next" (text='Next', class='pageNav-jump--next') also matches the
    # generic .pageNav-jump selector, and its href is /page-2, which would
    # incorrectly cap our walk at page 2.
    for anchor in tree.cssselect("a.pageNav-jump, a[href*='goto/last']"):
        href = anchor.get("href", "")
        text = (anchor.text or "").strip().lower()
        cls = (anchor.get("class", "") or "").lower()
        is_last = (
            "last" in text
            or "pageNav-jump--last" in cls
            or "goto/last" in href
        )
        if not is_last:
            continue
        page = _page_number_from_url(href)
        if page is not None and page > 1:
            return page

    # Strategy 2: largest numeric ``href`` inside the nav.
    # Skip "Next"/"Prev" anchors — they're not page-number targets.
    max_page = 1
    for nav in tree.cssselect(".pageNav, nav.pageNav, .pageNavWrapper"):
        for anchor in nav.cssselect("a[href]"):
            href = anchor.get("href", "")
            text = (anchor.text or "").strip().lower()
            cls = (anchor.get("class", "") or "").lower()
            if "next" in text or "prev" in text:
                continue
            if "pageNav-jump--next" in cls or "pageNav-jump--prev" in cls:
                continue
            page = _page_number_from_url(href)
            if page is not None and page > max_page:
                max_page = page
    return max_page


def extract_links_from_posts(page_html: str) -> list[str]:
    """Pull ``<a href>`` values from inside post bodies.

    v2 selectors first, then v1 fallbacks. We deliberately
    *don't* restrict by class name on the anchor itself —
    the wrapper is the trust boundary, not the anchor.
    """
    try:
        tree = lxml_html.fromstring(page_html)
    except Exception:
        return []

    # v2: <div class="bbWrapper"> ... </div>
    containers = tree.cssselect("div.bbWrapper")
    if not containers:
        # v1: <div class="messageContent"> ... </div>
        containers = tree.cssselect("div.messageContent")
    if not containers:
        # Last-ditch: the entire post block.
        containers = tree.cssselect("article.message, .message-body")

    hrefs: list[str] = []
    for container in containers:
        for anchor in container.cssselect("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            # Skip in-page anchors, javascript:, mailto:, etc.
            if href.startswith(("#", "javascript:", "mailto:")):
                continue
            hrefs.append(href)
    return hrefs


class XenForoThreadParser(ForumThreadParser):
    """XenForo-flavored :class:`ForumThreadParser`.

    The friendly-URL style is the only URL style we render:
    ``/threads/<slug>.<id>/page-N``. Forums that disable
    friendly URLs and use ``?page=N`` instead can override
    :meth:`build_page_url`.
    """

    forum_key: str = ""  # set by the concrete subclass

    def detect_last_page(self, page1_html: str, page1_url: str) -> int:
        return detect_last_page_from_html(page1_html)

    def build_page_url(self, thread_base_url: str, page_number: int) -> str:
        """Render ``(thread_base, page)`` -> ``.../page-N`` URL.

        For page 1 we return ``thread_base_url`` unchanged
        (XenForo serves page 1 at both ``/`` and ``/page-1``,
        and the bare URL is what the operator typically pastes).
        """
        if page_number <= 1:
            return thread_base_url
        # Strip any existing /page-N suffix so we don't stack.
        base = _PAGE_PATH_RE.sub("", thread_base_url)
        # Ensure exactly one trailing slash before appending.
        if not base.endswith("/"):
            base += "/"
        return f"{base}page-{page_number}"

    def extract_post_links(self, page_html: str) -> list[str]:
        return extract_links_from_posts(page_html)
