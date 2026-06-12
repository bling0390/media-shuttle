"""Unit tests for the ``task.completed`` notification fan-out.

The core-worker publishes a JSON event to
``media_shuttle:event_task_completed`` once a task transitions to
``SUCCEEDED``. The telegram bot subscribes to that key and forwards
the event to the requester. The test below pins the publish-side
contract:

* the event includes the four required fields
  (task_id, requester_id, file_name, size_bytes)
* the event is only published when the task is actually succeeded
  (failure path is not in scope here — see #1995)
* a missing ``requester_id`` in the task doc is logged and skipped,
  not raised

We don't talk to a real redis here — we monkey-patch
``_publish_task_completed_event`` to record the payload into a list
and assert on it. The redis client wrapper is exercised in the
docker stack during the e2e test in this commit.
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


class PublishOnSucceedTests(unittest.TestCase):
    def setUp(self) -> None:
        # Replace the publish helper with a recorder. We do not want
        # these tests to depend on a running redis.
        self._published: list[dict] = []
        self._patcher = mock.patch.object(
            core_tasks, "_publish_task_completed_event",
            side_effect=lambda payload: self._published.append(payload),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_succeeded_publishes_event_with_file_and_size(self):
        repo = mock.MagicMock()
        repo.get.return_value = _make_record("1076750810")
        service = _build_service(repo)

        upload_results = [
            {
                "ok": True,
                "location": "rclone://115:/foo/bar.mp4",
                "download": {
                    "file_name": "bar.mp4",
                    "size_bytes": 1234567890,
                },
            }
        ]
        result = core_tasks.process_finalize_task_logic(
            upload_results=upload_results,
            event={"task_id": "abc", "attempt": 0},
            task_id="abc",
            app=None,
            service=service,
        )

        self.assertEqual(result["state"], "succeeded")
        repo.update_status.assert_called_once()
        self.assertEqual(len(self._published), 1)
        ev = self._published[0]
        self.assertEqual(ev["task_id"], "abc")
        self.assertEqual(ev["requester_id"], "1076750810")
        self.assertEqual(ev["file_name"], "bar.mp4")
        self.assertEqual(ev["size_bytes"], 1234567890)

    def test_succeeded_skips_publish_when_requester_missing(self):
        repo = mock.MagicMock()
        repo.get.return_value = _make_record(None)  # no requester
        service = _build_service(repo)

        upload_results = [
            {
                "ok": True,
                "location": "rclone://115:/foo/bar.mp4",
                "download": {"file_name": "bar.mp4", "size_bytes": 1},
            }
        ]
        core_tasks.process_finalize_task_logic(
            upload_results=upload_results,
            event={"task_id": "abc", "attempt": 0},
            task_id="abc",
            app=None,
            service=service,
        )

        # No event published; the finalize itself still succeeded.
        self.assertEqual(self._published, [])
        repo.update_status.assert_called_once()
        args, _kwargs = repo.update_status.call_args
        # update_status(task_id, status, message="") — first arg is
        # task_id, second is the TaskStatus we transitioned to.
        self.assertEqual(args[0], "abc")
        self.assertEqual(args[1], core_tasks.TaskStatus.SUCCEEDED)

    def test_succeeded_skips_publish_when_filename_missing(self):
        # The download packet was malformed (no file_name). We still
        # mark the task SUCCEEDED — the upload itself returned ok —
        # but we have nothing useful to notify about.
        repo = mock.MagicMock()
        repo.get.return_value = _make_record("1076750810")
        service = _build_service(repo)

        upload_results = [
            {
                "ok": True,
                "location": "rclone://115:/x/y",
                "download": {"size_bytes": 0},  # no file_name
            }
        ]
        core_tasks.process_finalize_task_logic(
            upload_results=upload_results,
            event={"task_id": "abc", "attempt": 0},
            task_id="abc",
            app=None,
            service=service,
        )

        self.assertEqual(self._published, [])


class PublishHelperIsBestEffortTests(unittest.TestCase):
    """The publish wrapper must never raise into the finalize path."""

    def test_redis_unreachable_does_not_raise(self):
        # Simulate redis-py raising on RPUSH. The helper should
        # swallow it so the SUCCEEDED transition is the source of
        # truth and a flaky notification channel cannot roll the
        # task back to a non-terminal state.
        with mock.patch.object(core_tasks, "_redis_url", return_value="redis://127.0.0.1:1/0"):
            # Should not raise.
            core_tasks._publish_task_completed_event(
                {"task_id": "x", "requester_id": "1", "file_name": "a", "size_bytes": 1}
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
