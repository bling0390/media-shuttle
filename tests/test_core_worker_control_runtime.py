import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.queue import worker_control_runtime
from core.queue.worker_control_runtime import apply_worker_control


class TestCoreWorkerControlRuntime(unittest.TestCase):
    def setUp(self):
        worker_control_runtime._PROCS.clear()

    def test_start_should_spawn_managed_role_worker(self):
        class _Proc:
            pid = 9988

            def poll(self):
                return None

            def terminate(self):
                return None

            def kill(self):
                return None

        with patch("core.queue.worker_control_runtime.start_celery_process", return_value=_Proc()):
            out = apply_worker_control({"action": "start", "role": "download", "node_id": "", "concurrency": 2})

        self.assertTrue(out["accepted"])
        self.assertEqual(out["role"], "download")
        self.assertEqual(out["state"], "starting")

    def test_should_reject_unsupported_role(self):
        out = apply_worker_control({"action": "start", "role": "control", "node_id": "", "concurrency": 1})
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "unsupported_role")

    def test_should_reject_node_mismatch(self):
        with patch("core.queue.worker_control_runtime._resolve_owner_node", return_value="NODE_B"):
            out = apply_worker_control({"action": "start", "role": "parse", "node_id": "NODE_A", "concurrency": 1})
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "node_mismatch")

    def test_stop_should_terminate_running_proc(self):
        class _Proc:
            pid = 1122

            def __init__(self):
                self._code = None

            def poll(self):
                return self._code

            def terminate(self):
                self._code = 0
                return None

            def kill(self):
                self._code = -9
                return None

        with patch("core.queue.worker_control_runtime.start_celery_process", return_value=_Proc()):
            start = apply_worker_control({"action": "start", "role": "upload", "node_id": "", "concurrency": 1})
        self.assertTrue(start["accepted"])

        stop = apply_worker_control({"action": "stop", "role": "upload", "node_id": "", "concurrency": 1})
        self.assertTrue(stop["accepted"])
        self.assertEqual(stop["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
