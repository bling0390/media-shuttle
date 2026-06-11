"""Unit tests for the local-download cleanup behaviour.

The worker downloads to ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` (default
``/tmp/media-shuttle``) and is supposed to delete the file as
soon as upload completes. Two paths are easy to miss:

1. A task that fails upload and then exhausts its retry budget
   must still clean up on the last attempt — otherwise the
   local file is a permanent disk drain.
2. A worker that died mid-download (OOM, SIGKILL) leaves a
   ``tmp.part`` on disk; a fresh supervisor must sweep the
   download root on boot.

The tests below pin both behaviours.

Run with:
    cd media-shuttle-core && python -m unittest tests.test_cleanup
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.utils import cleanup_local_download


class CleanupLocalDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        # Sandbox the download root in a tempdir so the test
        # never reaches the real ``/tmp/media-shuttle`` (which is
        # the worker's actual download area in dev).
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env_patch = patch.dict(
            os.environ, {"MEDIA_SHUTTLE_DOWNLOAD_DIR": str(self.root)}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _touch(self, rel: str) -> Path:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"hello world")
        return target

    def test_removes_file_inside_root(self) -> None:
        f = self._touch("abc123/tmp.part")
        self.assertTrue(f.exists())
        self.assertTrue(cleanup_local_download(str(f)))
        self.assertFalse(f.exists())

    def test_removes_seed_dir_and_file(self) -> None:
        f = self._touch("c5d0b305c084e570/tmp.part")
        seed_dir = f.parent
        self.assertTrue(seed_dir.exists())
        self.assertTrue(cleanup_local_download(str(f)))
        # cleanup_local_download also prunes empty parent dirs
        # up to (but not including) the download root.
        self.assertFalse(seed_dir.exists())

    def test_refuses_path_outside_root(self) -> None:
        # Path outside ``MEDIA_SHUTTLE_DOWNLOAD_DIR`` must NOT
        # be deleted, even by mistake. Pin that the safety net
        # is doing its job.
        outside = Path(tempfile.gettempdir()) / "definitely-not-ours.txt"
        outside.write_bytes(b"keep me")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        self.assertFalse(cleanup_local_download(str(outside)))
        self.assertTrue(outside.exists())

    def test_disabled_via_env(self) -> None:
        f = self._touch("abc/tmp.part")
        with patch.dict(os.environ, {"MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS": "0"}):
            self.assertFalse(cleanup_local_download(str(f)))
        self.assertTrue(f.exists(), "file should still be on disk when cleanup is disabled")


class SweepOrphanedDownloadsTests(unittest.TestCase):
    """The supervisor's pre-boot sweep must clear any partial
    downloads left behind by a previous worker process."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env_patch = patch.dict(
            os.environ, {"MEDIA_SHUTTLE_DOWNLOAD_DIR": str(self.root)}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_sweep_removes_per_source_seed_dirs(self) -> None:
        from core.queue.worker_process import _sweep_orphaned_downloads

        for seed in ("aaa", "bbb", "ccc"):
            (self.root / seed).mkdir()
            (self.root / seed / "tmp.part").write_bytes(b"orphan")
        # An unrelated file in the root must be left alone.
        (self.root / "stray.txt").write_bytes(b"keep me")
        self.addCleanup(lambda: (self.root / "stray.txt").unlink(missing_ok=True))

        removed = _sweep_orphaned_downloads()
        self.assertEqual(removed, 3)
        for seed in ("aaa", "bbb", "ccc"):
            self.assertFalse((self.root / seed).exists())
        # The unrelated file survives.
        self.assertTrue((self.root / "stray.txt").exists())

    def test_sweep_is_safe_when_root_missing(self) -> None:
        from core.queue.worker_process import _sweep_orphaned_downloads

        # Point at a non-existent directory; should noop cleanly
        # rather than blow up the supervisor boot.
        with patch.dict(os.environ, {"MEDIA_SHUTTLE_DOWNLOAD_DIR": "/nonexistent-root-xyz"}):
            self.assertEqual(_sweep_orphaned_downloads(), 0)


if __name__ == "__main__":
    unittest.main()
