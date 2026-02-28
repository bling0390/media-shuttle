import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.bootstrap import build_core_service
from core.enums import SourceSite
from core.queue.celery_app import TASK_UPLOAD_RESULT, route_task
from core.queue.tasks import process_download_source_logic
from core.queue.worker_process import generate_queue_names


class TestCoreUploadAffinity(unittest.TestCase):
    def test_route_task_should_route_upload_to_owner_queue(self):
        old_affinity = os.getenv("MEDIA_SHUTTLE_UPLOAD_AFFINITY")
        old_prefix = os.getenv("MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY")
        os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = "1"
        os.environ["MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY"] = "media_shuttle:task_upload"
        try:
            route = route_task(
                TASK_UPLOAD_RESULT,
                args=[{"owner_node": "node-a"}, "task-1", "RCLONE", "/dest"],
            )
        finally:
            if old_affinity is None:
                os.environ.pop("MEDIA_SHUTTLE_UPLOAD_AFFINITY", None)
            else:
                os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = old_affinity
            if old_prefix is None:
                os.environ.pop("MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY", None)
            else:
                os.environ["MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY"] = old_prefix

        self.assertIsNotNone(route)
        self.assertEqual(route["queue"], "media_shuttle:task_upload@RCLONE@NODE-A")
        self.assertEqual(route["routing_key"], "media_shuttle:task_upload@RCLONE@NODE-A")

    def test_route_task_should_fallback_to_generic_upload_queue_without_owner(self):
        old_affinity = os.getenv("MEDIA_SHUTTLE_UPLOAD_AFFINITY")
        os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = "1"
        try:
            route = route_task(TASK_UPLOAD_RESULT, args=[{}, "task-1", "RCLONE", "/dest"])
        finally:
            if old_affinity is None:
                os.environ.pop("MEDIA_SHUTTLE_UPLOAD_AFFINITY", None)
            else:
                os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = old_affinity

        self.assertIsNotNone(route)
        self.assertEqual(route["queue"], "media_shuttle:task_upload@RCLONE")

    def test_download_logic_should_attach_owner_node(self):
        service = build_core_service()
        source = {
            "site": SourceSite.GENERIC.value,
            "page_url": "https://example.com/file.mp4",
            "download_url": "https://example.com/file.mp4",
            "file_name": "file.mp4",
            "remote_folder": "demo",
            "metadata": {},
        }

        out = process_download_source_logic(
            event={"spec_version": "task.created.v1"},
            task_id="task-affinity-1",
            source=source,
            service=service,
            owner_node="node_b",
        )

        self.assertTrue(out["ok"])
        self.assertEqual(out["owner_node"], "NODE_B")

    def test_upload_worker_queues_should_include_owner_specific_suffix(self):
        old_affinity = os.getenv("MEDIA_SHUTTLE_UPLOAD_AFFINITY")
        old_node = os.getenv("MEDIA_SHUTTLE_NODE_ID")
        old_targets = os.getenv("MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES")
        os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = "1"
        os.environ["MEDIA_SHUTTLE_NODE_ID"] = "srv-a"
        os.environ["MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES"] = "RCLONE"
        try:
            queues = generate_queue_names("upload")
        finally:
            if old_affinity is None:
                os.environ.pop("MEDIA_SHUTTLE_UPLOAD_AFFINITY", None)
            else:
                os.environ["MEDIA_SHUTTLE_UPLOAD_AFFINITY"] = old_affinity
            if old_node is None:
                os.environ.pop("MEDIA_SHUTTLE_NODE_ID", None)
            else:
                os.environ["MEDIA_SHUTTLE_NODE_ID"] = old_node
            if old_targets is None:
                os.environ.pop("MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES", None)
            else:
                os.environ["MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES"] = old_targets

        self.assertIn("media_shuttle:task_upload@RCLONE", queues)
        self.assertIn("media_shuttle:task_upload@RCLONE@SRV-A", queues)


if __name__ == "__main__":
    unittest.main()
