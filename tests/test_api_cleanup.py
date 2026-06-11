"""Unit tests for the api-side manual local-download cleanup.

The api exposes ``POST /v1/admin/cleanup-downloads`` and
backs the ``/leech cleanup`` Telegram command. The handler
walks ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` and removes every direct
child (each child is a single ``<sha1-seed>/tmp.part`` tree
produced by the worker). The tests below pin the behaviour
the operator will see in a Telegram reply.

Run with:
    python -m unittest tests.test_api_cleanup
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-api").resolve()))

from app.cleanup import sweep_download_dir  # noqa: E402
from app.service import ApiService  # noqa: E402
from app.repository import TaskRepository  # noqa: E402
from app.queue import TaskPublisher  # noqa: E402
from app.worker_control import WorkerControl  # noqa: E402


def _touch(root: Path, rel: str, payload: bytes = b"x") -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


class CleanupSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env_patch = patch.dict(
            os.environ, {"MEDIA_SHUTTLE_DOWNLOAD_DIR": str(self.root)}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _make_service(self) -> ApiService:
        return ApiService(
            repository=TaskRepository(),
            publisher=TaskPublisher(),
            worker_repository=TaskRepository(),
            worker_control=WorkerControl(),
        )

    def test_empty_root_returns_zero_counters(self) -> None:
        result = sweep_download_dir(dry_run=False)
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["items"], [])

    def test_dry_run_reports_but_does_not_delete(self) -> None:
        _touch(self.root, "seed-a/tmp.part", b"a" * 2048)
        _touch(self.root, "seed-b/tmp.part", b"b" * 4096)
        _touch(self.root, "stray.log", b"c" * 1024)

        result = sweep_download_dir(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["freed_bytes"], 0)
        # Nothing was actually removed.
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()),
            ["seed-a", "seed-b", "stray.log"],
        )

    def test_real_run_removes_seed_dirs_and_reports_freed(self) -> None:
        _touch(self.root, "seed-a/tmp.part", b"a" * 2048)
        _touch(self.root, "seed-b/tmp.part", b"b" * 4096)
        _touch(self.root, "stray.log", b"c" * 1024)

        result = sweep_download_dir(dry_run=False)
        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["removed"], 3)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["freed_bytes"], 2048 + 4096 + 1024)
        # Root must be empty (or contain only new files)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_outside_path_is_rejected(self) -> None:
        # A symlink that points outside the root must be
        # rejected so the sweep can never rm -rf the host.
        outside = Path(tempfile.gettempdir()) / "outside_target.txt"
        outside.write_bytes(b"keep me")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / "evil-link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not supported: {exc}")

        result = sweep_download_dir(dry_run=False)
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(outside.exists(), "outside target must survive")
        self.assertEqual(
            result["items"][0]["reason"], "outside_download_root"
        )

    def test_service_returns_accepted_envelope(self) -> None:
        _touch(self.root, "seed-a/tmp.part", b"a" * 1024)
        service = self._make_service()
        result = service.admin_cleanup_downloads_action(dry_run=False)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["freed_bytes"], 1024)
        # Sweep ran through the service as well.
        self.assertEqual(list(self.root.iterdir()), [])

    def test_service_dry_run_does_not_delete(self) -> None:
        _touch(self.root, "seed-a/tmp.part", b"a" * 1024)
        service = self._make_service()
        result = service.admin_cleanup_downloads_action(dry_run=True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["removed"], 0)
        self.assertTrue((self.root / "seed-a" / "tmp.part").exists())


if __name__ == "__main__":
    unittest.main()
