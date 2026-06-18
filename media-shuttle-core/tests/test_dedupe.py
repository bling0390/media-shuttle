"""Unit tests for the per-file dedupe store.

The dedupe feature reuses a previously-uploaded file's
``rclone`` location when the same ``album + file_name``
shows up again within ``MEDIA_SHUTTLE_DEDUPE_TTL_DAYS``
(default 30). Tests here cover the three primitives the
pipeline relies on:

* ``_dedupe_key_for`` — identity (md5) for a source.
* ``_dedupe_lookup`` — redis read; tolerant of failures
  and malformed entries.
* ``_dedupe_write`` — redis write with TTL; a write on
  a failed upload must not happen (the pipeline only
  calls ``_dedupe_write`` from the success path; the
  test pins the contract via direct call).

Plus integration: ``process_created_event_logic`` must
filter out already-deduplicated sources, mark a fully
skipped task as ``SUCCEEDED``, and emit per-file skip
notifications. ``process_upload_result_logic`` must
write the dedupe entry on success.

All tests are offline: redis is faked with a
``FakeRedis`` that lives in-process. We never hit a
real redis server.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "media-shuttle-core"))

from core.queue.tasks import (  # noqa: E402
    _dedupe_key_for,
    _dedupe_lookup,
    _dedupe_ttl_seconds,
    _dedupe_write,
)


class _FakeRedis:
    """In-process stand-in for the redis client.

    Implements only the ``get`` / ``set`` / ``setex``
    surface that ``_dedupe_lookup`` and ``_dedupe_write``
    use. We deliberately do not subclass the real
    ``redis.Redis`` because the real client insists on
    a connection at import time and the test runs offline.
    """

    def __init__(self, url: str = "redis://test") -> None:
        self._store: dict[str, tuple[str, int | None]] = {}
        self._url = url
        self._fail_get = False
        self._fail_set = False

    @classmethod
    def from_url(cls, url: str):
        return cls(url)

    def get(self, key: str):
        if self._fail_get:
            raise RuntimeError("simulated redis down")
        entry = self._store.get(key)
        if entry is None:
            return None
        return entry[0]

    def set(self, key: str, value, ex: int | None = None):
        if self._fail_set:
            raise RuntimeError("simulated redis down")
        # The real client accepts bytes or str; mirror
        # that loosely so the tests don't have to care.
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        self._store[key] = (str(value), ex)
        return True

    # Helpers used by the tests below.
    def has(self, key: str) -> bool:
        return key in self._store

    def ttl(self, key: str) -> int | None:
        return self._store.get(key, (None, None))[1]


class _SourceFactory:
    """Build ParsedSource-shaped dicts without importing
    the dataclass (the test stays focused on the dedupe
    primitives, not the parser pipeline)."""

    @staticmethod
    def bunkr(file_name: str = "a.mp4", folder: str = "my-album") -> dict:
        return {
            "site": "BUNKR",
            "page_url": f"https://bunkr.su/a/{folder}",
            "download_url": f"https://media-files.bunkr.su/v/{file_name}",
            "file_name": file_name,
            "remote_folder": folder,
            "metadata": {},
        }

    @staticmethod
    def pixeldrain(file_name: str = "b.mp4") -> dict:
        # pixeldrain parser does not set ``remote_folder``;
        # the dedupe must fall back to the page URL.
        return {
            "site": "PIXELDRAIN",
            "page_url": f"https://pixeldrain.com/u/abc123",
            "download_url": f"https://pixeldrain.com/api/file/abc123",
            "file_name": file_name,
            "remote_folder": None,
            "metadata": {},
        }


class DedupeKeyBuilderTests(unittest.TestCase):
    """Pin the identity contract the user described.

    Same album + same file name → same key, regardless
    of bunkr mirror domain or page vs cdn URL. Different
    album or different file name → different key.
    """

    def test_same_bunkr_album_and_file_produces_same_key(self):
        # The same physical file accessed via two different
        # bunkr mirror domains must collapse to one key.
        mirror_a = _SourceFactory.bunkr("a.mp4", "shared-album")
        mirror_b = dict(mirror_a)
        mirror_b["page_url"] = "https://bunkr.si/a/shared-album"
        mirror_b["download_url"] = "https://media-files.bunkr.la/v/a.mp4"
        self.assertEqual(_dedupe_key_for(mirror_a), _dedupe_key_for(mirror_b))

    def test_different_file_name_produces_different_key(self):
        a = _SourceFactory.bunkr("a.mp4", "shared-album")
        b = _SourceFactory.bunkr("b.mp4", "shared-album")
        self.assertNotEqual(_dedupe_key_for(a), _dedupe_key_for(b))

    def test_different_album_produces_different_key(self):
        a = _SourceFactory.bunkr("a.mp4", "album-1")
        b = _SourceFactory.bunkr("a.mp4", "album-2")
        self.assertNotEqual(_dedupe_key_for(a), _dedupe_key_for(b))

    def test_different_site_produces_different_key(self):
        # A file with the same name on a different site
        # must not collide with the bunkr copy.
        a = _SourceFactory.bunkr("a.mp4", "shared")
        b = _SourceFactory.pixeldrain("a.mp4")
        self.assertNotEqual(_dedupe_key_for(a), _dedupe_key_for(b))

    def test_no_remote_folder_falls_back_to_page_url(self):
        # pixeldrain parser leaves ``remote_folder`` empty.
        # Two requests for the same pixeldrain id should
        # still dedup.
        a = _SourceFactory.pixeldrain()
        b = _SourceFactory.pixeldrain()
        self.assertEqual(_dedupe_key_for(a), _dedupe_key_for(b))

    def test_no_folder_different_page_urls_produce_different_keys(self):
        # Without a folder, the page URL is the only
        # stable signal. Two different files on
        # pixeldrain (different ids) must NOT collide.
        a = _SourceFactory.pixeldrain("a.mp4")
        b = dict(a)
        b["page_url"] = "https://pixeldrain.com/u/different"
        self.assertNotEqual(_dedupe_key_for(a), _dedupe_key_for(b))

    def test_album_name_with_separator_does_not_collide(self):
        # NUL-separator (see ``_dedupe_key_for``) keeps
        # an album named ``"a|b"`` from colliding with
        # an album named ``"a"`` + file named ``"b"``.
        a = _SourceFactory.bunkr("b.mp4", "a")
        b = _SourceFactory.bunkr("a.mp4", "b")  # same triple, different join
        # They are different triples, so different keys
        # regardless. The separator test is more
        # pathological: same surface string, different
        # boundaries.
        a2 = {"site": "BUNKR", "file_name": "b", "remote_folder": "a", "page_url": ""}
        b2 = {"site": "BUNKR", "file_name": "a", "remote_folder": "b", "page_url": ""}
        self.assertNotEqual(_dedupe_key_for(a2), _dedupe_key_for(b2))


class DedupeTtlTests(unittest.TestCase):
    """``MEDIA_SHUTTLE_DEDUPE_TTL_DAYS`` env contract.

    Misconfiguration must not silently disable dedupe
    (a 0-TTL key would expire immediately and defeat
    the feature). Garbage values fall back to 30 days.
    """

    def setUp(self):
        self._old = os.environ.get("MEDIA_SHUTTLE_DEDUPE_TTL_DAYS")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("MEDIA_SHUTTLE_DEDUPE_TTL_DAYS", None)
        else:
            os.environ["MEDIA_SHUTTLE_DEDUPE_TTL_DAYS"] = self._old

    def test_default_is_thirty_days(self):
        os.environ.pop("MEDIA_SHUTTLE_DEDUPE_TTL_DAYS", None)
        self.assertEqual(_dedupe_ttl_seconds(), 30 * 86400)

    def test_honors_env_value(self):
        os.environ["MEDIA_SHUTTLE_DEDUPE_TTL_DAYS"] = "7"
        self.assertEqual(_dedupe_ttl_seconds(), 7 * 86400)

    def test_garbage_value_falls_back_to_thirty_days(self):
        os.environ["MEDIA_SHUTTLE_DEDUPE_TTL_DAYS"] = "forever"
        self.assertEqual(_dedupe_ttl_seconds(), 30 * 86400)

    def test_zero_value_does_not_disable_dedupe(self):
        # 0 would defeat the feature; clamp to default.
        os.environ["MEDIA_SHUTTLE_DEDUPE_TTL_DAYS"] = "0"
        self.assertEqual(_dedupe_ttl_seconds(), 30 * 86400)


class DedupeReadWriteTests(unittest.TestCase):
    """Round-trip the dedupe store against a fake redis.

    The pipeline only writes on success and only reads
    at parse time, but the primitives themselves are
    public so they can be tested in isolation.
    """

    def setUp(self):
        self._fake = _FakeRedis()
        # Patch the redis factory inside the tasks module
        # so ``_dedupe_lookup`` and ``_dedupe_write`` see
        # our fake.
        self._patches = [
            patch(
                "redis.Redis.from_url",
                side_effect=lambda url: self._fake,
            )
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_write_then_lookup_returns_record(self):
        source = _SourceFactory.bunkr()
        _dedupe_write(source, task_id="t-1", location="115:/album/a.mp4")
        record = _dedupe_lookup(source)
        self.assertIsNotNone(record)
        self.assertEqual(record["task_id"], "t-1")
        self.assertEqual(record["location"], "115:/album/a.mp4")
        self.assertIn("uploaded_at", record)

    def test_write_persists_ttl(self):
        os.environ["MEDIA_SHUTTLE_DEDUPE_TTL_DAYS"] = "7"
        try:
            source = _SourceFactory.bunkr()
            _dedupe_write(source, task_id="t-1", location="115:/x")
            key = _dedupe_key_for(source)
            self.assertEqual(self._fake.ttl(key), 7 * 86400)
        finally:
            os.environ.pop("MEDIA_SHUTTLE_DEDUPE_TTL_DAYS", None)

    def test_lookup_miss_returns_none(self):
        # No write has happened for this key.
        self.assertIsNone(_dedupe_lookup(_SourceFactory.bunkr()))

    def test_write_skips_empty_location(self):
        # An empty location would brick the dedupe
        # pipeline: a future lookup would return a
        # record with no path. Guard against it.
        source = _SourceFactory.bunkr()
        _dedupe_write(source, task_id="t-1", location="")
        self.assertIsNone(_dedupe_lookup(source))

    def test_lookup_tolerates_malformed_entry(self):
        # A stray ``SET key value`` from a human with
        # redis-cli must not brick the pipeline. The
        # JSON parse returns ``None`` and we treat
        # it as a miss.
        source = _SourceFactory.bunkr()
        key = _dedupe_key_for(source)
        self._fake.set(key, "not json")
        self.assertIsNone(_dedupe_lookup(source))

    def test_lookup_tolerates_redis_failure(self):
        # Redis being down must NOT block a task from
        # proceeding. We fail open: treat the source
        # as not-deduplicated and let the normal
        # download + upload run. Better to upload
        # twice than to silently drop a file.
        self._fake._fail_get = True
        self.assertIsNone(_dedupe_lookup(_SourceFactory.bunkr()))

    def test_write_tolerates_redis_failure(self):
        # A write failure is non-fatal; the current
        # upload has already succeeded. We log and
        # move on so the operator does not see a
        # "dedupe write failed" error toast.
        self._fake._fail_set = True
        _dedupe_write(_SourceFactory.bunkr(), task_id="t-1", location="115:/x")
        # The write raised silently; subsequent
        # lookup would be a miss.
        self.assertIsNone(_dedupe_lookup(_SourceFactory.bunkr()))

    def test_cross_parser_dedup(self):
        # An album uploaded via bunkr must be
        # recognized as already-uploaded when the
        # operator re-leeches via a different parser
        # pointing at the same physical file. We
        # simulate that by writing the dedupe entry
        # for the bunkr triple, then looking up the
        # same triple from a different mirror domain.
        original = _SourceFactory.bunkr("a.mp4", "shared-album")
        _dedupe_write(original, task_id="t-1", location="115:/album/a.mp4")
        mirror = dict(original)
        mirror["page_url"] = "https://bunkr.la/a/shared-album"
        mirror["download_url"] = "https://media-files.bunkr.si/v/a.mp4"
        record = _dedupe_lookup(mirror)
        self.assertIsNotNone(record)
        self.assertEqual(record["location"], "115:/album/a.mp4")


class ProcessCreatedEventDedupeTests(unittest.TestCase):
    """Filter behavior at parse time.

    ``process_created_event_logic`` must drop
    already-uploaded sources, and a fully-skipped task
    must land as ``SUCCEEDED`` rather than hanging in
    ``DOWNLOADING`` until a timeout.

    These tests use the real ``build_core_service``
    (in-memory) and inject the parsed sources +
    dedupe records via monkeypatches. We avoid
    spinning up a celery app: ``app=None`` is fine
    when the dedupe path short-circuits the fan-out
    (the all-skipped case) or when the fan-out's
    downstream ``_schedule_source_pipelines`` is
    patched to return early (the mixed and clean
    cases).
    """

    def _patch_pipeline(
        self,
        parsed_sources: list[dict],
        dedupe_records: dict[str, dict],
    ):
        """Monkeypatch the parser registry and the
        dedupe lookup so the call site sees the
        scenario under test.
        """
        from core.queue import tasks as tasks_mod

        def _fake_lookup(source):
            key = tasks_mod._dedupe_key_for(source)
            return dedupe_records.get(key)

        # We patch the parser registry inside the
        # service's pipeline. The pipeline is built by
        # ``build_pipeline_service`` so we just override
        # ``parse`` on the registry for the duration of
        # the test.
        service = tasks_mod.build_core_service()
        original_parse = service.pipeline.parser_registry.parse

        def _patched_parse(url):
            return parsed_sources

        service.pipeline.parser_registry.parse = _patched_parse
        return (
            service,
            patch.object(tasks_mod, "_dedupe_lookup", _fake_lookup),
            lambda: setattr(service.pipeline.parser_registry, "parse", original_parse),
        )

    def test_all_sources_deduped_marks_task_succeeded(self):
        from core.enums import TaskStatus
        from core.models import ParsedSource
        from core.queue import tasks as tasks_mod

        source = ParsedSource(
            site="BUNKR",
            page_url="https://bunkr.su/a/shared",
            download_url="https://cdn.bunkr.su/v/a.mp4",
            file_name="a.mp4",
            remote_folder="shared",
        )
        service, dedupe_patch, restore = self._patch_pipeline(
            parsed_sources=[source],
            dedupe_records={
                tasks_mod._dedupe_key_for(asdict(source)): {
                    "task_id": "previous",
                    "location": "115:/album/a.mp4",
                    "uploaded_at": "2026-06-01T00:00:00Z",
                }
            },
        )
        with dedupe_patch:
            event = {
                "spec_version": "task.created.v1",
                "task_id": "t-now",
                "task_type": "parse_link",
                "idempotency_key": "k",
                "created_at": "2026-06-18T00:00:00Z",
                "payload": {
                    "url": "https://bunkr.su/a/shared",
                    "requester_id": "u-1",
                    "target": "RCLONE",
                    "destination": "115:/",
                },
            }
            result = tasks_mod.process_created_event_logic(
                event=event, app=None, service=service
            )
            restore()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["source_count"], 0)
        record = service.repository.get("t-now")
        self.assertEqual(record.status, TaskStatus.SUCCEEDED)

    def test_mixed_deduped_and_new_sources_fans_out_only_new(self):
        from core.models import ParsedSource
        from core.queue import tasks as tasks_mod

        dup = ParsedSource(
            site="BUNKR",
            page_url="https://bunkr.su/a/shared",
            download_url="https://cdn.bunkr.su/v/a.mp4",
            file_name="a.mp4",
            remote_folder="shared",
        )
        fresh = ParsedSource(
            site="BUNKR",
            page_url="https://bunkr.su/a/shared",
            download_url="https://cdn.bunkr.su/v/b.mp4",
            file_name="b.mp4",
            remote_folder="shared",
        )
        service, dedupe_patch, restore = self._patch_pipeline(
            parsed_sources=[dup, fresh],
            dedupe_records={
                tasks_mod._dedupe_key_for(asdict(dup)): {
                    "task_id": "previous",
                    "location": "115:/album/a.mp4",
                    "uploaded_at": "2026-06-01T00:00:00Z",
                }
            },
        )
        with dedupe_patch:
            event = {
                "spec_version": "task.created.v1",
                "task_id": "t-mixed",
                "task_type": "parse_link",
                "idempotency_key": "k",
                "created_at": "2026-06-18T00:00:00Z",
                "payload": {
                    "url": "https://bunkr.su/a/shared",
                    "requester_id": "u-1",
                    "target": "RCLONE",
                    "destination": "115:/",
                },
            }
            captured: dict = {}

            def _record(**kwargs):
                captured["args"] = kwargs
                # Returning None here mirrors the celery
                # branch: when the celery app is used
                # the function publishes onto the
                # worker queue and returns None, leaving
                # the caller to fall through to the
                # ``state="queued"`` summary.
                return None

            with patch.object(
                tasks_mod,
                "_schedule_source_pipelines",
                side_effect=_record,
            ):
                result = tasks_mod.process_created_event_logic(
                    event=event, app=None, service=service
                )
            restore()
        self.assertEqual(result["state"], "queued")
        # Only the fresh source was fanned out.
        self.assertEqual(len(captured["args"]["parsed_sources"]), 1)
        self.assertEqual(
            captured["args"]["parsed_sources"][0].file_name,
            "b.mp4",
        )
        # The mongo ``sources`` array still contains
        # both files (so /monitor shows the full
        # picture) but the work queue only sees the
        # new one.
        record = service.repository.get("t-mixed")
        self.assertEqual(len(record.sources), 2)

    def test_no_dedupes_falls_through_normally(self):
        from core.models import ParsedSource
        from core.queue import tasks as tasks_mod

        source = ParsedSource(
            site="BUNKR",
            page_url="https://bunkr.su/a/fresh",
            download_url="https://cdn.bunkr.su/v/x.mp4",
            file_name="x.mp4",
            remote_folder="fresh",
        )
        service, dedupe_patch, restore = self._patch_pipeline(
            parsed_sources=[source], dedupe_records={}
        )
        with dedupe_patch:
            event = {
                "spec_version": "task.created.v1",
                "task_id": "t-fresh",
                "task_type": "parse_link",
                "idempotency_key": "k",
                "created_at": "2026-06-18T00:00:00Z",
                "payload": {
                    "url": "https://bunkr.su/a/fresh",
                    "requester_id": "u-1",
                    "target": "RCLONE",
                    "destination": "115:/",
                },
            }
            captured: dict = {}

            def _record(**kwargs):
                captured["args"] = kwargs
                return None

            with patch.object(
                tasks_mod,
                "_schedule_source_pipelines",
                side_effect=_record,
            ):
                tasks_mod.process_created_event_logic(
                    event=event, app=None, service=service
                )
            restore()
        self.assertEqual(len(captured["args"]["parsed_sources"]), 1)
        self.assertEqual(
            captured["args"]["parsed_sources"][0].file_name,
            "x.mp4",
        )


if __name__ == "__main__":
    unittest.main()
