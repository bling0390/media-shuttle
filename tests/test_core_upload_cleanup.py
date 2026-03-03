import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.bootstrap import build_core_service
from core.queue.tasks import process_upload_result_logic


class TestCoreUploadCleanup(unittest.TestCase):
    def test_service_run_task_should_cleanup_local_download_after_upload_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR")
            old_cleanup = os.getenv("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS")
            os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = tmp
            os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = "1"
            try:
                service = build_core_service()
                event = {
                    "spec_version": "task.created.v1",
                    "task_id": "cleanup-service-1",
                    "task_type": "parse_link",
                    "idempotency_key": "idem-cleanup-service-1",
                    "created_at": "2026-03-03T00:00:00Z",
                    "payload": {
                        "url": "https://example.com/cleanup.mp4",
                        "requester_id": "u-cleanup",
                        "target": "RCLONE",
                        "destination": "/dest",
                    },
                }
                service.create_task_from_event(event)
                result = service.run_task("cleanup-service-1")
            finally:
                if old_dir is None:
                    os.environ.pop("MEDIA_SHUTTLE_DOWNLOAD_DIR", None)
                else:
                    os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = old_dir
                if old_cleanup is None:
                    os.environ.pop("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = old_cleanup

            self.assertEqual(result.status.value, "SUCCEEDED")
            self.assertEqual(list(Path(tmp).rglob("*")), [])

    def test_upload_result_logic_should_keep_file_when_cleanup_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp) / "abc123"
            local_dir.mkdir(parents=True, exist_ok=True)
            local_file = local_dir / "tmp.part"
            local_file.write_bytes(b"payload")

            old_dir = os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR")
            old_cleanup = os.getenv("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS")
            os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = tmp
            os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = "0"
            try:
                out = process_upload_result_logic(
                    download_packet={
                        "ok": True,
                        "download": {
                            "site": "GENERIC",
                            "source_url": "https://example.com/file.mp4",
                            "local_path": str(local_file),
                            "size_bytes": 7,
                            "file_name": "file.mp4",
                            "remote_folder": "abc123",
                        },
                        "source": {
                            "site": "GENERIC",
                            "page_url": "https://example.com/file.mp4",
                            "download_url": "https://example.com/file.mp4",
                            "file_name": "file.mp4",
                            "remote_folder": "abc123",
                            "metadata": {},
                        },
                    },
                    task_id="cleanup-task-1",
                    target="RCLONE",
                    destination="/dest",
                    service=build_core_service(),
                )
            finally:
                if old_dir is None:
                    os.environ.pop("MEDIA_SHUTTLE_DOWNLOAD_DIR", None)
                else:
                    os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = old_dir
                if old_cleanup is None:
                    os.environ.pop("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = old_cleanup

            self.assertTrue(out["ok"])
            self.assertTrue(local_file.exists())

    def test_upload_result_logic_should_cleanup_file_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_dir = Path(tmp) / "xyz999"
            local_dir.mkdir(parents=True, exist_ok=True)
            local_file = local_dir / "tmp.part"
            local_file.write_bytes(b"payload")

            old_dir = os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR")
            old_cleanup = os.getenv("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS")
            os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = tmp
            os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = "1"
            try:
                out = process_upload_result_logic(
                    download_packet={
                        "ok": True,
                        "download": {
                            "site": "GENERIC",
                            "source_url": "https://example.com/file.mp4",
                            "local_path": str(local_file),
                            "size_bytes": 7,
                            "file_name": "file.mp4",
                            "remote_folder": "xyz999",
                        },
                        "source": {
                            "site": "GENERIC",
                            "page_url": "https://example.com/file.mp4",
                            "download_url": "https://example.com/file.mp4",
                            "file_name": "file.mp4",
                            "remote_folder": "xyz999",
                            "metadata": {},
                        },
                    },
                    task_id="cleanup-task-2",
                    target="RCLONE",
                    destination="/dest",
                    service=build_core_service(),
                )
            finally:
                if old_dir is None:
                    os.environ.pop("MEDIA_SHUTTLE_DOWNLOAD_DIR", None)
                else:
                    os.environ["MEDIA_SHUTTLE_DOWNLOAD_DIR"] = old_dir
                if old_cleanup is None:
                    os.environ.pop("MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CLEANUP_ON_UPLOAD_SUCCESS"] = old_cleanup

            self.assertTrue(out["ok"])
            self.assertFalse(local_file.exists())
            self.assertFalse(local_dir.exists())


if __name__ == "__main__":
    unittest.main()
