import os
import signal
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

    def test_generate_control_queue_names_should_include_node_specific_queue(self):
        old_node = os.getenv("MEDIA_SHUTTLE_NODE_ID")
        old_control = os.getenv("MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY")
        os.environ["MEDIA_SHUTTLE_NODE_ID"] = "node-a"
        os.environ["MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY"] = "media_shuttle:worker_control"
        try:
            queues = generate_queue_names("control")
        finally:
            if old_node is None:
                os.environ.pop("MEDIA_SHUTTLE_NODE_ID", None)
            else:
                os.environ["MEDIA_SHUTTLE_NODE_ID"] = old_node
            if old_control is None:
                os.environ.pop("MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY", None)
            else:
                os.environ["MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY"] = old_control

        self.assertIn("media_shuttle:worker_control", queues)
        self.assertIn("media_shuttle:worker_control@NODE-A", queues)

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

    def test_run_forever_should_spawn_parse_download_upload_control_workers_when_role_all(self):
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

        with patch("core.queue.worker_process.subprocess.Popen", side_effect=[_Proc(0), _Proc(0), _Proc(0), _Proc(0)]) as popen:
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
        self.assertEqual(popen.call_count, 4)
        cmd1 = popen.call_args_list[0].args[0]
        cmd2 = popen.call_args_list[1].args[0]
        cmd3 = popen.call_args_list[2].args[0]
        cmd4 = popen.call_args_list[3].args[0]
        self.assertIn("--hostname=core-worker-parse@media-shuttle-core", cmd1)
        self.assertIn("--hostname=core-worker-download@media-shuttle-core", cmd2)
        self.assertIn("--hostname=core-worker-upload@media-shuttle-core", cmd3)
        self.assertIn("--hostname=core-worker-control@media-shuttle-core", cmd4)

    def test_run_forever_should_update_worker_registry_status(self):
        class _Proc:
            pid = 12345

            def wait(self):
                return 0

            def poll(self):
                return 0

            def terminate(self):
                return None

            def kill(self):
                return None

        class _Registry:
            def __init__(self):
                self.calls = []

            def upsert_worker(self, **kwargs):
                self.calls.append(kwargs)
                return kwargs

        registry = _Registry()
        old_role = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE")
        os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = "parse"
        try:
            with patch("core.queue.worker_process._build_worker_registry", return_value=registry), patch(
                "core.queue.worker_process.subprocess.Popen", return_value=_Proc()
            ):
                rc = run_forever()
        finally:
            if old_role is None:
                os.environ.pop("MEDIA_SHUTTLE_CORE_WORKER_ROLE", None)
            else:
                os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = old_role

        self.assertEqual(rc, 0)
        self.assertGreaterEqual(len(registry.calls), 3)
        statuses = [item["status"] for item in registry.calls]
        self.assertIn("STARTING", statuses)
        self.assertIn("READY", statuses)
        self.assertIn("SHUTDOWN", statuses)

    def test_run_forever_should_mark_shutdown_on_sigterm(self):
        class _Proc:
            def __init__(self):
                self.pid = 22334
                self._code = None

            def wait(self):
                self._code = 0 if self._code is None else self._code
                return self._code

            def poll(self):
                return self._code

            def terminate(self):
                self._code = 0
                return None

            def kill(self):
                self._code = -9
                return None

        class _Registry:
            def __init__(self):
                self.calls = []

            def upsert_worker(self, **kwargs):
                self.calls.append(kwargs)
                return kwargs

        registry = _Registry()

        def _wait_for_any_exit(_procs, on_tick=None):
            # Run in the same thread so run_forever-installed handler can intercept SIGTERM.
            os.kill(os.getpid(), signal.SIGTERM)
            return _procs[0], 0

        old_role = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE")
        os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = "parse"
        try:
            with patch("core.queue.worker_process._build_worker_registry", return_value=registry), patch(
                "core.queue.worker_process.subprocess.Popen", return_value=_Proc()
            ), patch("core.queue.worker_process._wait_for_any_exit", side_effect=_wait_for_any_exit):
                rc = run_forever()
        finally:
            if old_role is None:
                os.environ.pop("MEDIA_SHUTTLE_CORE_WORKER_ROLE", None)
            else:
                os.environ["MEDIA_SHUTTLE_CORE_WORKER_ROLE"] = old_role

        self.assertEqual(rc, 143)
        shutdown_calls = [item for item in registry.calls if item.get("status") == "SHUTDOWN"]
        self.assertTrue(shutdown_calls)
        self.assertTrue(any("SIGTERM" in (item.get("reason") or "") for item in shutdown_calls))


if __name__ == "__main__":
    unittest.main()
