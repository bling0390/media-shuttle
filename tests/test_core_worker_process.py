import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.queue.worker_process import generate_queue_names, run_forever, start_celery_process


class TestCoreWorkerProcess(unittest.TestCase):
    def test_generate_download_queue_names_should_include_site_suffixes(self):
        old_sites = os.getenv("MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES")
        os.environ["MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES"] = "GOFILE,BUNKR"
        try:
            queues = generate_queue_names("download")
        finally:
            if old_sites is None:
                os.environ.pop("MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES", None)
            else:
                os.environ["MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES"] = old_sites

        self.assertIn("media_shuttle:task_download@GOFILE", queues)
        self.assertIn("media_shuttle:task_download@BUNKR", queues)

    def test_generate_parse_queue_names_should_include_created_and_retry(self):
        queues = generate_queue_names("parse")
        self.assertIn("media_shuttle:task_created", queues)
        self.assertIn("media_shuttle:task_retry", queues)

    def test_start_celery_process_should_use_role_queue_and_hostname(self):
        class _Proc:
            def wait(self):
                return 0

            def poll(self):
                return 0

        with patch("core.queue.worker_process.subprocess.Popen", return_value=_Proc()) as popen:
            start_celery_process("parse")

        args = popen.call_args.args[0]
        self.assertIn("--hostname=core-worker-parse@media-shuttle-core", args)
        self.assertIn("--queues=media_shuttle:task_retry,media_shuttle:task_created", args)

    def test_run_forever_should_spawn_parse_download_upload_workers_when_role_all(self):
        class _Proc:
            def __init__(self, code):
                self._code = code

            def wait(self):
                return self._code

            def poll(self):
                return self._code

            def terminate(self):
                return None

            def kill(self):
                return None

        with patch("core.queue.worker_process.subprocess.Popen", side_effect=[_Proc(0), _Proc(0), _Proc(0)]) as popen:
            old_role = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE")
            os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = "all"
            try:
                rc = run_forever()
            finally:
                if old_role is None:
                    os.environ.pop("MEDIA_SHUTTLE_CORE_WORKER_ROLE", None)
                else:
                    os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = old_role

        self.assertEqual(rc, 0)
        self.assertEqual(popen.call_count, 3)
        cmd1 = popen.call_args_list[0].args[0]
        cmd2 = popen.call_args_list[1].args[0]
        cmd3 = popen.call_args_list[2].args[0]
        self.assertIn("--hostname=core-worker-parse@media-shuttle-core", cmd1)
        self.assertIn("--hostname=core-worker-download@media-shuttle-core", cmd2)
        self.assertIn("--hostname=core-worker-upload@media-shuttle-core", cmd3)


if __name__ == "__main__":
    unittest.main()
