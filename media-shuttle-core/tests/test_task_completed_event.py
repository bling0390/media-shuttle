"""Unit tests for the ``task.completed`` notification fan-out.

The core-worker publishes a JSON event to
``media_shuttle:event_task_completed`` once each individual
upload (success or failure) finishes. The telegram bot
subscribes to that key and forwards the event to the
requester. The test below pins the publish-side contract:

* the event includes the required fields
  (task_id, requester_id, file_name, size_bytes, ok)
* the event is published for every per-file outcome, not
  just at the album-level finalize
* a missing ``requester_id`` is logged and skipped, not raised
* the finalize callback no longer publishes a summary
  (per-file events are the only signal the tg bot needs)

We don't talk to a real redis here — we monkey-patch
``_publish_task_completed_event`` to record the payload into
a list and assert on it. The redis client wrapper is
exercised in the docker stack during the e2e test.
"""

from __future__ import annotations

import unittest
from unittest import mock

from core.queue import tasks as core_tasks


def _build_service(repo, *, requester_id="1076750810"):
    service = mock.MagicMock()
    service.repository = repo
    service.pipeline = mock.MagicMock()
    return service


def _make_record(requester_id: str | None):
    rec = mock.MagicMock()
    rec.requester_id = requester_id
    return rec


class PerSourcePublishOnSuccessTests(unittest.TestCase):
    """``process_upload_result_logic`` publishes one event per file."""

    def setUp(self) -> None:
        # Replace the publish helper with a recorder. We do not
        # want these tests to depend on a running redis.
        self._published: list[dict] = []
        self._patcher = mock.patch.object(
            core_tasks,
            "_publish_task_completed_event",
            side_effect=lambda payload: self._published.append(payload),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def _build_service_with_uploader(self, repo, uploader_return, side_effect=None):
        service = mock.MagicMock()
        service.repository = repo
        # Stub the uploader so we don't actually try to upload.
        # ``side_effect`` is applied after ``return_value`` so
        # an exception override wins for the failure tests.
        service.pipeline.uploader_registry.upload.return_value = uploader_return
        if side_effect is not None:
            service.pipeline.uploader_registry.upload.side_effect = side_effect
        return service

    def test_upload_success_publishes_event_with_ok_true(self):
        repo = mock.MagicMock()
        service = self._build_service_with_uploader(
            repo,
            mock.MagicMock(location="rclone://115:/x/bar.mp4"),
        )

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": True,
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 1234567890,
                    "site": "BUNKR",
                    "source_url": "https://cdn.cr/x.mp4",
                    "local_path": "/tmp/x.part",
                },
                "source": {},
                "event": {
                    "task_id": "abc",
                    "payload": {"requester_id": "1076750810"},
                    "created_at": "2026-06-14T00:00:00+00:00",
                },
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(len(self._published), 1)
        ev = self._published[0]
        self.assertEqual(ev["task_id"], "abc")
        self.assertEqual(ev["requester_id"], "1076750810")
        self.assertEqual(ev["file_name"], "bar.mp4")
        self.assertEqual(ev["size_bytes"], 1234567890)
        self.assertTrue(ev["ok"])
        self.assertEqual(ev["target"], "RCLONE")
        self.assertEqual(ev["source_site"], "BUNKR")
        self.assertEqual(ev["location"], "rclone://115:/x/bar.mp4")

    def test_upload_failure_publishes_event_with_ok_false(self):
        repo = mock.MagicMock()
        service = self._build_service_with_uploader(
            repo,
            mock.MagicMock(location="rclone://115:/x/bar.mp4"),
            side_effect=RuntimeError("rclone: connection reset"),
        )

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": True,
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 1234,
                    "site": "BUNKR",
                    "source_url": "https://cdn.cr/x.mp4",
                    "local_path": "/tmp/x.part",
                },
                "source": {},
                "event": {
                    "task_id": "abc",
                    "payload": {"requester_id": "1076750810"},
                },
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(len(self._published), 1)
        ev = self._published[0]
        self.assertFalse(ev["ok"])
        self.assertIn("rclone: connection reset", ev["reason"])

    def test_download_failure_still_publishes_event(self):
        # If the download itself failed (bunkr 404 etc.) the
        # upload step is skipped, but we still want a per-file
        # notification so the operator sees the failure
        # without waiting for the album-level finalize.
        repo = mock.MagicMock()
        service = _build_service(repo)

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": False,
                "reason": "404 Not Found",
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 0,
                    "site": "BUNKR",
                },
                "event": {
                    "task_id": "abc",
                    "payload": {"requester_id": "1076750810"},
                },
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(len(self._published), 1)
        ev = self._published[0]
        self.assertFalse(ev["ok"])
        self.assertIn("404 Not Found", ev["reason"])

    def test_publish_skips_when_requester_id_missing(self):
        # Event payload has no requester_id and mongo record
        # also has none. No notification, but upload result
        # is still returned so the parent pipeline keeps going.
        from core.models import TaskRecord, TaskPayload, TaskStatus

        record = TaskRecord(
            task_id="abc",
            idempotency_key="k",
            payload=TaskPayload(
                url="https://example.com/x",
                requester_id="",
                target="RCLONE",
                destination="115:/x",
            ),
            status=TaskStatus.DOWNLOADING,
        )
        repo = mock.MagicMock()
        repo.get.return_value = record
        service = self._build_service_with_uploader(
            repo,
            mock.MagicMock(location="rclone://115:/x/bar.mp4"),
        )

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": True,
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 1,
                    "site": "BUNKR",
                    "source_url": "https://cdn.cr/x.mp4",
                    "local_path": "/tmp/x.part",
                },
                "event": {"task_id": "abc"},  # no payload
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(self._published, [])

    def test_publish_uses_mongo_fallback_when_event_lacks_requester(self):
        # Event payload was stripped (e.g. an event that came
        # in via redis without the payload key). Mongo holds
        # the requester_id on the parent task record, so the
        # fallback should resolve.
        from core.models import TaskRecord, TaskPayload, TaskStatus

        record = TaskRecord(
            task_id="abc",
            idempotency_key="k",
            payload=TaskPayload(
                url="https://example.com/x",
                requester_id="555000111",
                target="RCLONE",
                destination="115:/x",
            ),
            status=TaskStatus.DOWNLOADING,
        )
        repo = mock.MagicMock()
        repo.get.return_value = record
        service = self._build_service_with_uploader(
            repo,
            mock.MagicMock(location="rclone://115:/x/bar.mp4"),
        )

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": True,
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 1,
                    "site": "BUNKR",
                    "source_url": "https://cdn.cr/x.mp4",
                    "local_path": "/tmp/x.part",
                },
                "event": {"task_id": "abc"},  # no payload
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(len(self._published), 1)
        self.assertEqual(self._published[0]["requester_id"], "555000111")

    def test_publish_skips_when_file_name_missing(self):
        # The download packet was malformed (no file_name).
        # Skip the notification — there's nothing meaningful
        # to send — but keep the upload result so the parent
        # pipeline doesn't stall. We exercise the file_name
        # guard in ``_publish_per_source_notification`` by
        # driving the download_failed path, where the
        # DownloadResult constructor is bypassed entirely.
        repo = mock.MagicMock()
        service = _build_service(repo)

        core_tasks.process_upload_result_logic(
            download_packet={
                "ok": False,
                "reason": "broken metadata",
                "download": {
                    "size_bytes": 0,
                    "site": "BUNKR",
                    # file_name intentionally missing
                },
                "event": {
                    "task_id": "abc",
                    "payload": {"requester_id": "1076750810"},
                },
            },
            task_id="abc",
            target="RCLONE",
            destination="115:/x",
            service=service,
        )

        self.assertEqual(self._published, [])


class FinalizeNoLongerPublishesTests(unittest.TestCase):
    """``process_finalize_task_logic`` must NOT publish a summary.

    The per-file events from ``process_upload_result_logic`` are
    the only signal the tg bot needs. Publishing a summary at
    finalize would duplicate the message for multi-file albums.
    """

    def setUp(self) -> None:
        self._published: list[dict] = []
        self._patcher = mock.patch.object(
            core_tasks,
            "_publish_task_completed_event",
            side_effect=lambda payload: self._published.append(payload),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_finalize_does_not_publish_on_succeed(self):
        repo = mock.MagicMock()
        repo.get.return_value = _make_record("1076750810")
        service = _build_service(repo)

        result = core_tasks.process_finalize_task_logic(
            upload_results=[
                {
                    "ok": True,
                    "location": "rclone://115:/foo/bar.mp4",
                    "download": {"file_name": "bar.mp4", "size_bytes": 1234},
                }
            ],
            event={
                "task_id": "abc",
                "attempt": 0,
                "payload": {"requester_id": "1076750810"},
            },
            task_id="abc",
            app=None,
            service=service,
        )

        # Finalize succeeded (status moved to SUCCEEDED) but
        # no notification was published.
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self._published, [])

    def test_finalize_does_not_publish_on_fail(self):
        # Failure path: per-file notifications have already
        # gone out from process_upload_result_logic for any
        # failed files. Finalize just transitions the parent
        # task to FAILED and routes the failure event.
        repo = mock.MagicMock()
        repo.get.return_value = _make_record("1076750810")
        service = _build_service(repo)
        # ``_route_failure`` looks at ``app.send_task``; in
        # production this is a celery app. We pass a mock so
        # the test doesn't need a real broker.
        fake_app = mock.MagicMock()
        fake_app.send_task.return_value = mock.MagicMock(task_id="retry-1")

        result = core_tasks.process_finalize_task_logic(
            upload_results=[
                {
                    "ok": False,
                    "reason": "boom",
                    "download": {"file_name": "bar.mp4", "size_bytes": 1234},
                }
            ],
            event={"task_id": "abc", "attempt": 0, "payload": {"requester_id": "1076750810"}},
            task_id="abc",
            app=fake_app,
            service=service,
        )

        # ``_route_failure`` re-queues a retry when attempt <
        # max_retries (production default: 3). We don't care
        # which terminal state the failure takes in this test
        # — we only care that no notification was published.
        self.assertIn(result["state"], ("failed", "retried"))
        self.assertEqual(self._published, [])


class PublishHelperIsBestEffortTests(unittest.TestCase):
    """The publish wrapper must never raise into the upload path."""

    def test_redis_unreachable_does_not_raise(self):
        # Simulate redis-py raising on RPUSH. The helper should
        # swallow it so the SUCCEEDED transition is the source of
        # truth and a flaky notification channel cannot roll the
        # upload result back.
        with mock.patch.object(
            core_tasks, "_redis_url", return_value="redis://127.0.0.1:1/0"
        ):
            # Should not raise.
            core_tasks._publish_task_completed_event(
                {
                    "task_id": "x",
                    "requester_id": "1",
                    "file_name": "a",
                    "size_bytes": 1,
                }
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
