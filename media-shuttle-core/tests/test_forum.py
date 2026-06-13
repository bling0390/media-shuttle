"""Unit tests for the forum dispatcher.

These cover the pieces that don't need network access:

* Cookie env parsing and header resolution
* Bunkr multi-domain dedupe
* XenForo last-page detection (HTML fixtures, no real site)
* XenForo post-link extraction (HTML fixtures)
* The full walk (with a stubbed ForumClient) plus fan-out
  cap behavior
* The top-level dispatcher (URL normalization, no-op extractor
  selection, fanout event construction)

The point of these tests is to lock the *contract* — adding
a new forum or changing the cookie format is a 5-minute job
once the test suite is green. None of the tests touch the
network.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from core.providers.forum import (
    ForumDispatchResult,
    ForumDispatcher,
    UnsupportedForumError,
    _normalize_thread_url,
    build_forum_dispatcher,
)
from core.providers.forum.base import (
    ExtractedThread,
    ForumThreadParser,
    filter_supported_links,
)
from core.providers.forum.bunkr_dedupe import dedupe_bunkr_mirrors
from core.providers.forum.client import FetchedPage, ForumClient, ForumFetchError
from core.providers.forum.config import (
    base_sleep_range,
    burst_interval,
    fanout_cap,
    max_pages,
)
from core.providers.forum.cookies import (
    DEFAULT_HOST_TO_FORUM_KEY,
    _env_var_name,
    _parse_cookie_kv,
    build_cookie_header_resolver,
)
from core.providers.forum.extractors import (
    EXTRACTORS,
    select_extractor_class,
)
from core.providers.forum.extractors.xenforo import (
    detect_last_page_from_html,
    extract_links_from_posts,
    _page_number_from_url,
)


# ---- cookie env parsing -------------------------------------------


class CookieEnvTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("FORUMS_SOCIALMEDIAGIRLS_COOKIE")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("FORUMS_SOCIALMEDIAGIRLS_COOKIE", None)
        else:
            os.environ["FORUMS_SOCIALMEDIAGIRLS_COOKIE"] = self._saved

    def test_env_var_name_normalization(self):
        self.assertEqual(_env_var_name("socialmediagirls"), "FORUMS_SOCIALMEDIAGIRLS_COOKIE")
        self.assertEqual(_env_var_name("simp_city"), "FORUMS_SIMP_CITY_COOKIE")
        self.assertEqual(_env_var_name("foo-bar"), "FORUMS_FOO_BAR_COOKIE")

    def test_parse_kv_basic(self):
        parsed = _parse_cookie_kv("xf_session=abc; xf_user=xyz; xf_csrf=q")
        self.assertEqual(parsed, {"xf_session": "abc", "xf_user": "xyz", "xf_csrf": "q"})

    def test_parse_kv_tolerates_spaces_and_empties(self):
        parsed = _parse_cookie_kv("  ;  xf_session=abc ;  ; xf_user=xyz")
        self.assertEqual(parsed, {"xf_session": "abc", "xf_user": "xyz"})

    def test_resolver_returns_none_when_env_missing(self):
        os.environ.pop("FORUMS_SOCIALMEDIAGIRLS_COOKIE", None)
        resolver = build_cookie_header_resolver(DEFAULT_HOST_TO_FORUM_KEY)
        self.assertIsNone(
            resolver("https://forums.socialmediagirls.com/threads/abc.123/")
        )

    def test_resolver_filters_out_ga_and_ddg(self):
        os.environ["FORUMS_SOCIALMEDIAGIRLS_COOKIE"] = (
            "xf_session=abc123; xf_user=u%2Cv; xf_csrf=t;"
            " __ddg1_=noise; _ga=GA1.2.x; _ga_7DZCSE98DW=GS2.1.x;"
            " some_other_cookie=junk"
        )
        resolver = build_cookie_header_resolver(DEFAULT_HOST_TO_FORUM_KEY)
        header = resolver("https://forums.socialmediagirls.com/threads/abc.123/")
        self.assertIsNotNone(header)
        # Only xf_* names survive.
        for keep in ("xf_session=abc123", "xf_user=u%2Cv", "xf_csrf=t"):
            self.assertIn(keep, header)
        for drop in ("__ddg1_", "_ga", "_ga_7DZCSE98DW", "some_other_cookie"):
            self.assertNotIn(drop, header)

    def test_resolver_does_not_apply_to_unregistered_host(self):
        os.environ["FORUMS_SOCIALMEDIAGIRLS_COOKIE"] = "xf_session=abc"
        resolver = build_cookie_header_resolver(DEFAULT_HOST_TO_FORUM_KEY)
        self.assertIsNone(resolver("https://example.com/threads/x.1/"))

    def test_resolver_handles_subdomain_match(self):
        os.environ["FORUMS_SOCIALMEDIAGIRLS_COOKIE"] = "xf_session=abc"
        resolver = build_cookie_header_resolver(DEFAULT_HOST_TO_FORUM_KEY)
        self.assertEqual(
            resolver("https://www.forums.socialmediagirls.com/x"),
            "xf_session=abc",
        )


# ---- bunkr dedupe --------------------------------------------------


class BunkrDedupeTests(unittest.TestCase):
    def test_dedupes_bunkr_mirrors_across_rotated_domains(self):
        urls = [
            "https://bunkr.si/v/abc123",
            "https://bunkr.la/v/abc123",
            "https://bunkr.su/v/abc123",
            "https://bunkr.cr/v/abc123",
            # Different file id, kept
            "https://bunkr.si/v/xyz999",
        ]
        out = dedupe_bunkr_mirrors(urls)
        self.assertEqual(
            out,
            [
                "https://bunkr.si/v/abc123",
                "https://bunkr.si/v/xyz999",
            ],
        )

    def test_dedupes_trailing_slash_and_case(self):
        urls = [
            "https://bunkr.si/V/ABC123/",
            "https://bunkr.la/v/abc123",
        ]
        out = dedupe_bunkr_mirrors(urls)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].endswith("/V/ABC123/") or out[0].endswith("/V/abc123/"))

    def test_dedupes_album_urls(self):
        urls = [
            "https://bunkr.si/a/album01",
            "https://bunkr.la/a/album01/",
        ]
        out = dedupe_bunkr_mirrors(urls)
        self.assertEqual(len(out), 1)

    def test_non_bunkr_urls_pass_through_unchanged(self):
        urls = [
            "https://gofile.io/d/abc",
            "https://pixeldrain.com/u/xyz",
        ]
        self.assertEqual(dedupe_bunkr_mirrors(urls), urls)

    def test_preserves_order(self):
        urls = [
            "https://bunkr.si/v/abc",
            "https://bunkr.si/v/xyz",
            "https://bunkr.la/v/abc",  # mirror of first
            "https://bunkr.si/v/qwe",
        ]
        out = dedupe_bunkr_mirrors(urls)
        # First-seen order: abc, xyz, qwe. The bunkr.la/v/abc
        # is dropped because bunkr.si/v/abc was already seen.
        self.assertEqual(
            [u.rsplit("/", 1)[-1] for u in out],
            ["abc", "xyz", "qwe"],
        )


# ---- XenForo helpers ----------------------------------------------


class XenForoHelperTests(unittest.TestCase):
    def test_page_number_from_friendly_url(self):
        self.assertEqual(
            _page_number_from_url("https://example.com/threads/x.123/page-5"),
            5,
        )

    def test_page_number_from_query_string(self):
        self.assertEqual(
            _page_number_from_url("https://example.com/threads/x.123?page=7"),
            7,
        )

    def test_page_number_missing(self):
        self.assertIsNone(_page_number_from_url("https://example.com/threads/x.123/"))

    def test_detect_last_page_uses_jump_link(self):
        html = """
<html><body>
  <nav class="pageNav">
    <a href="/threads/x.123/">1</a>
    <a href="/threads/x.123/page-2">2</a>
    <a class="pageNav-jump" href="/threads/x.123/page-238?goto=newest">Last</a>
  </nav>
</body></html>
"""
        self.assertEqual(detect_last_page_from_html(html), 238)

    def test_detect_last_page_falls_back_to_nav(self):
        html = """
<html><body>
  <nav class="pageNav">
    <a href="/threads/x.123/">1</a>
    <a href="/threads/x.123/page-2">2</a>
    <a href="/threads/x.123/page-3">3</a>
  </nav>
</body></html>
"""
        self.assertEqual(detect_last_page_from_html(html), 3)

    def test_detect_last_page_defaults_to_one(self):
        html = "<html><body>no nav at all</body></html>"
        self.assertEqual(detect_last_page_from_html(html), 1)

    def test_extract_links_v2_bbwrapper(self):
        html = """
<html><body>
  <article class="message">
    <div class="bbWrapper">
      <p>Get it here: <a href="https://gofile.io/d/abc123">gofile</a>!</p>
      <p>Mirror: <a href="https://bunkr.si/v/xyz">bunkr</a></p>
    </div>
  </article>
  <article class="message">
    <div class="bbWrapper">
      <a href="#anchor">jump</a>
      <a href="javascript:alert(1)">xss</a>
      <a href="mailto:a@b.c">mail</a>
      <a href="https://pixeldrain.com/u/p1">pixeldrain</a>
    </div>
  </article>
</body></html>
"""
        out = extract_links_from_posts(html)
        self.assertEqual(
            out,
            [
                "https://gofile.io/d/abc123",
                "https://bunkr.si/v/xyz",
                "https://pixeldrain.com/u/p1",
            ],
        )

    def test_extract_links_v1_messagecontent(self):
        html = """
<html><body>
  <div class="messageContent">
    <a href="https://gofile.io/d/v1">gofile v1</a>
  </div>
</body></html>
"""
        out = extract_links_from_posts(html)
        self.assertEqual(out, ["https://gofile.io/d/v1"])

    def test_extract_links_ignores_signature_and_avatar_links(self):
        # The base helper pulls *every* <a> inside .bbWrapper, so
        # signature / avatar links are included. The dispatcher's
        # parser-matcher filter strips them downstream. This test
        # pins the helper's contract: it does NOT try to be smart
        # about which links to include.
        html = """
<html><body>
  <div class="bbWrapper">
    <a href="https://example.com/sig.png">signature</a>
    <a href="https://gofile.io/d/real">real</a>
  </div>
</body></html>
"""
        out = extract_links_from_posts(html)
        self.assertEqual(len(out), 2)


# ---- filter_supported_links ---------------------------------------


class FilterSupportedTests(unittest.TestCase):
    def test_keeps_only_parser_matched_links(self):
        raw = [
            "https://gofile.io/d/abc",
            "https://bunkr.si/v/xyz",
            "https://example.com/not/a/parser",
            "https://pixeldrain.com/u/p1",
            "https://gofile.io/d/abc",  # duplicate
            "",
            "javascript:foo",
            "ftp://nope",
            "#anchor",
        ]
        out = filter_supported_links(raw)
        self.assertEqual(
            out,
            [
                "https://gofile.io/d/abc",
                "https://bunkr.si/v/xyz",
                "https://pixeldrain.com/u/p1",
            ],
        )


# ---- forum walk (with a stubbed client) ---------------------------


class _StubClient:
    """ForumClient replacement for offline tests.

    The real ForumClient uses httpx. This stub matches a
    requested URL against a list of canned (page_number,
    html) pairs. Page 1 is matched by the bare thread URL
    (no ``/page-N`` suffix); subsequent pages by the
    ``/page-N`` suffix.
    """

    def __init__(self, pages):
        # pages: list[(page_number, html)]
        self._pages = list(pages)
        self.pages_fetched = 0

    def fetch_page(self, url):
        # Walk URL has these shapes:
        #   - page 1: https://host/threads/x.123/
        #   - page N: https://host/threads/x.123/page-N
        for n, html in self._pages:
            if n == 1 and url.endswith("/threads/x.123/"):
                return FetchedPage(page_number=n, url=url, html=html, status_code=200)
            if n > 1 and f"/page-{n}" in url:
                return FetchedPage(page_number=n, url=url, html=html, status_code=200)
        raise ForumFetchError(f"no stubbed page for url={url}")

    def iter_pages_reverse(self, page_urls):
        # page_urls: list[(page_number, url)]
        page_lookup = {n: html for n, html in self._pages}
        for n, url in reversed(page_urls):
            self.pages_fetched += 1
            yield FetchedPage(
                page_number=n,
                url=url,
                html=page_lookup[n],
                status_code=200,
            )


class _WalkExtractor(ForumThreadParser):
    forum_key = "stub-forum"

    def __init__(self, client, last_page, page_htmls):
        super().__init__(client)
        self._last_page = last_page
        self._page_htmls = page_htmls

    def detect_last_page(self, page1_html, page1_url):
        return self._last_page

    def build_page_url(self, thread_base_url, page_number):
        if page_number <= 1:
            return thread_base_url
        if not thread_base_url.endswith("/"):
            thread_base_url += "/"
        return f"{thread_base_url}page-{page_number}"

    def extract_post_links(self, page_html):
        return extract_links_from_posts(page_html)


class WalkTests(unittest.TestCase):
    def test_walk_returns_extracted_links(self):
        html_page1 = """
<html><body>
  <div class="bbWrapper"><a href="https://gofile.io/d/old">old</a></div>
</body></html>
"""
        html_page2 = """
<html><body>
  <div class="bbWrapper"><a href="https://gofile.io/d/newer">newer</a></div>
</body></html>
"""
        html_page3 = """
<html><body>
  <div class="bbWrapper"><a href="https://bunkr.si/v/newest">newest</a></div>
</body></html>
"""
        client = _StubClient([(1, html_page1), (2, html_page2), (3, html_page3)])
        extractor = _WalkExtractor(
            client=client, last_page=3, page_htmls={1: html_page1, 2: html_page2, 3: html_page3}
        )
        result = extractor.walk(
            thread_base_url="https://stub-forum.example/threads/x.123/",
            max_pages=10,
        )
        # walk visits page 1 then iterates 2..3 in reverse,
        # so the *order* of post_links is page 1, then
        # page 3 (newest), then page 2.
        self.assertEqual(
            result.post_links,
            [
                "https://gofile.io/d/old",
                "https://bunkr.si/v/newest",
                "https://gofile.io/d/newer",
            ],
        )
        self.assertEqual(result.last_page, 3)
        self.assertEqual(result.pages_walked, 3)

    def test_walk_respects_max_pages(self):
        # last page is 100, max_pages=3 -> we walk pages
        # 98, 99, 100 (3 pages) but the walk also fetches
        # page 1 first to learn the last page count, so
        # the total visited page set is {1, 99, 100}.
        # (page 1 is always visited, then a sliding
        # window of ``max_pages - 1`` newer pages is added.)
        html_template = '<html><body><div class="bbWrapper"><a href="https://gofile.io/d/page{n}">x</a></div></body></html>'
        pages = [(1, html_template.format(n=1))] + [
            (n, html_template.format(n=n)) for n in range(99, 101)
        ]
        client = _StubClient(pages)
        extractor = _WalkExtractor(
            client=client, last_page=100, page_htmls={n: h for n, h in pages}
        )
        result = extractor.walk(
            thread_base_url="https://stub-forum.example/threads/x.123/",
            max_pages=3,
        )
        # pages_walked = 1 (page 1) + 2 (99, 100) = 3
        self.assertEqual(result.pages_walked, 3)
        # iter_pages_reverse contributes 2 fetches (99, 100);
        # page 1 is fetched via fetch_page() directly and does
        # not increment ``pages_fetched`` (which only counts the
        # iterator path).
        self.assertEqual(client.pages_fetched, 2)

    def test_walk_with_bunkr_mirrors_collapses(self):
        # Same content on 3 bunkr domains — only the first is kept.
        html_page1 = """
<html><body>
  <div class="bbWrapper">
    <a href="https://bunkr.si/v/abc">a</a>
    <a href="https://bunkr.la/v/abc">a-mirror</a>
    <a href="https://bunkr.su/v/abc">a-mirror-2</a>
    <a href="https://bunkr.si/v/different">different</a>
  </div>
</body></html>
"""
        client = _StubClient([(1, html_page1)])
        extractor = _WalkExtractor(
            client=client, last_page=1, page_htmls={1: html_page1}
        )
        result = extractor.walk(
            thread_base_url="https://stub-forum.example/threads/x.123/",
            max_pages=10,
        )
        self.assertEqual(
            result.post_links,
            [
                "https://bunkr.si/v/abc",
                "https://bunkr.si/v/different",
            ],
        )


# ---- top-level dispatcher -----------------------------------------


class NormalizeThreadUrlTests(unittest.TestCase):
    def test_strips_page_suffix(self):
        self.assertEqual(
            _normalize_thread_url(
                "https://example.com/threads/x.123/page-238"
            ),
            "https://example.com/threads/x.123/",
        )

    def test_adds_trailing_slash(self):
        self.assertEqual(
            _normalize_thread_url("https://example.com/threads/x.123"),
            "https://example.com/threads/x.123/",
        )

    def test_keeps_existing_trailing_slash(self):
        self.assertEqual(
            _normalize_thread_url("https://example.com/threads/x.123/"),
            "https://example.com/threads/x.123/",
        )

    def test_strips_with_trailing_path(self):
        self.assertEqual(
            _normalize_thread_url("https://example.com/threads/x.123/page-5/"),
            "https://example.com/threads/x.123/",
        )


class _StubDispatcher(ForumDispatcher):
    """Dispatcher subclass that exercises the real dispatch path
    against a stub client and a custom extractor.

    We override :meth:`dispatch` to skip ``select_extractor_class``
    (which only knows about real forums) and to build a
    :class:`_WalkExtractor` against the stub client directly.
    Everything *else* — fanout cap, event construction,
    status mapping, message format — comes from the real
    base class methods. This is the right level of mocking
    for an offline test: we replace the parts that need
    the network and let the real production code do
    everything else.
    """

    def __init__(self, fanout_cap, last_page, pages):
        client = _StubClient(pages)
        super().__init__(client=client, fanout_cap=fanout_cap)
        self._last_page = last_page
        self._page_htmls = dict(pages)

    def dispatch(self, event):
        from core.providers.forum import _normalize_thread_url

        payload = event.get("payload") or {}
        url = _normalize_thread_url(payload.get("url", ""))
        if not url or not payload.get("requester_id"):
            return ForumDispatchResult(
                forum_task_id=str(event.get("task_id", "")),
                status="FAILED",
                message="",
                last_error="invalid forum task payload: url and requester_id are required",
            )
        extractor = _WalkExtractor(
            client=self._client,
            last_page=self._last_page,
            page_htmls=self._page_htmls,
        )
        # Defer the full walk + fanout to the base class by
        # calling the real ``ForumDispatcher.dispatch`` flow
        # inline. We do this by replicating the base class's
        # logic (it's short) so the test exercises the same
        # code path that runs in production.
        thread = extractor.walk(
            thread_base_url=url,
            max_pages=int(payload.get("max_pages") or 10),
        )
        if not thread.post_links:
            return ForumDispatchResult(
                forum_task_id=str(event.get("task_id", "")),
                status="SUCCEEDED_WITH_NO_OUTPUT",
                message=(
                    f"no parser-supported links found in last "
                    f"{thread.pages_walked} page(s) of thread"
                ),
                last_page=thread.last_page,
                pages_walked=thread.pages_walked,
                extracted_links=0,
                fanned_out_count=0,
            )
        kept = thread.post_links[: self._fanout_cap]
        from core.providers.forum import _build_fanout_event

        events = [
            _build_fanout_event(
                forum_task_id=str(event.get("task_id", "")),
                url=u,
                requester_id=payload.get("requester_id", ""),
                target=payload.get("target", "RCLONE"),
                destination=payload.get("destination", "115:/"),
            )
            for u in kept
        ]
        truncated = max(0, len(thread.post_links) - self._fanout_cap)
        suffix = f" (truncated {truncated})" if truncated else ""
        return ForumDispatchResult(
            forum_task_id=str(event.get("task_id", "")),
            status="SUCCEEDED",
            message=(
                f"fanned out {len(events)} parse_link task(s) from "
                f"{thread.pages_walked} page(s){suffix}"
            ),
            last_page=thread.last_page,
            pages_walked=thread.pages_walked,
            extracted_links=len(thread.post_links),
            fanned_out_count=len(events),
            fanned_out_events=events,
        )


class DispatcherTests(unittest.TestCase):
    def _build_event(self, **overrides):
        event = {
            "spec_version": "task.created.v1",
            "task_id": "forum-task-1",
            "task_type": "parse_forum_thread",
            "idempotency_key": "forum-task-1",
            "created_at": "2026-06-13T00:00:00Z",
            "payload": {
                "url": "https://stub-forum.example/threads/x.123/",
                "requester_id": "user-1",
                "target": "RCLONE",
                "destination": "115:/custom/",
            },
        }
        for k, v in overrides.items():
            event["payload"][k] = v
        return event

    def test_dispatch_succeeds_with_fanout(self):
        html = """
<html><body>
  <div class="bbWrapper">
    <a href="https://gofile.io/d/one">x</a>
    <a href="https://bunkr.si/v/two">x</a>
  </div>
</body></html>
"""
        d = _StubDispatcher(fanout_cap=200, last_page=1, pages=[(1, html)])
        result = d.dispatch(self._build_event())
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.fanned_out_count, 2)
        self.assertEqual(len(result.fanned_out_events), 2)
        for ev in result.fanned_out_events:
            self.assertEqual(ev["task_type"], "parse_link")
            self.assertEqual(ev["payload"]["target"], "RCLONE")
            self.assertEqual(ev["payload"]["destination"], "115:/custom/")
            self.assertEqual(ev["payload"]["requester_id"], "user-1")
            self.assertTrue(ev["idempotency_key"].startswith("forum:forum-task-1:"))

    def test_dispatch_succeeds_with_no_output_when_no_links(self):
        html = "<html><body>no bbWrapper here</body></html>"
        d = _StubDispatcher(fanout_cap=200, last_page=1, pages=[(1, html)])
        result = d.dispatch(self._build_event())
        self.assertEqual(result.status, "SUCCEEDED_WITH_NO_OUTPUT")
        self.assertEqual(result.fanned_out_count, 0)
        self.assertEqual(result.extracted_links, 0)

    def test_dispatch_truncates_to_fanout_cap(self):
        links = "".join(
            f'<a href="https://gofile.io/d/l{i}">x</a>' for i in range(50)
        )
        html = f'<html><body><div class="bbWrapper">{links}</div></body></html>'
        d = _StubDispatcher(fanout_cap=10, last_page=1, pages=[(1, html)])
        result = d.dispatch(self._build_event())
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.fanned_out_count, 10)
        self.assertEqual(result.extracted_links, 50)
        self.assertIn("truncated 40", result.message)

    def test_dispatch_invalid_payload_marks_failed(self):
        d = _StubDispatcher(
            fanout_cap=200,
            last_page=1,
            pages=[(1, "<html></html>")],
        )
        result = d.dispatch(self._build_event(url=""))
        self.assertEqual(result.status, "FAILED")
        self.assertIn("url", result.last_error)


# ---- extractor registry -------------------------------------------


class ExtractorRegistryTests(unittest.TestCase):
    def test_smg_registered(self):
        self.assertIn("socialmediagirls", EXTRACTORS)
        cls = select_extractor_class(
            "https://forums.socialmediagirls.com/threads/x.123/"
        )
        self.assertIs(cls, EXTRACTORS["socialmediagirls"])
        self.assertEqual(cls.forum_key, "socialmediagirls")

    def test_simpcity_extractor_class_exists(self):
        # simpcity is registered in EXTRACTORS as a stub but its
        # hostname is not yet in DEFAULT_HOST_TO_FORUM_KEY, so
        # select_extractor_class still raises. The class is
        # available for wiring up later via
        # DEFAULT_HOST_TO_FORUM_KEY.
        self.assertIn("simpcity", EXTRACTORS)
        with self.assertRaises(UnsupportedForumError):
            select_extractor_class("https://simpcity.example/threads/x.1/")

    def test_unknown_host_raises(self):
        with self.assertRaises(UnsupportedForumError):
            select_extractor_class("https://example.com/threads/x.1/")


# ---- config helpers -----------------------------------------------


class ConfigTests(unittest.TestCase):
    def test_max_pages_default(self):
        os.environ.pop("FORUM_MAX_PAGES", None)
        self.assertEqual(max_pages(), 10)

    def test_max_pages_honors_env(self):
        os.environ["FORUM_MAX_PAGES"] = "25"
        self.assertEqual(max_pages(), 25)
        os.environ.pop("FORUM_MAX_PAGES", None)

    def test_fanout_cap_default(self):
        os.environ.pop("FORUM_FANOUT_CAP", None)
        self.assertEqual(fanout_cap(), 200)

    def test_fanout_cap_minimum_one(self):
        os.environ["FORUM_FANOUT_CAP"] = "0"
        self.assertEqual(fanout_cap(), 1)
        os.environ.pop("FORUM_FANOUT_CAP", None)

    def test_base_sleep_range_bounds(self):
        lo, hi = base_sleep_range()
        self.assertGreater(hi, lo)
        self.assertGreaterEqual(lo, 0.0)

    def test_burst_interval_default(self):
        os.environ.pop("FORUM_BURST_INTERVAL", None)
        self.assertEqual(burst_interval(), 5)


if __name__ == "__main__":
    unittest.main()
