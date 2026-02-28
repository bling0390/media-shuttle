import json
import unittest
from pathlib import Path


class TestSpecs(unittest.TestCase):
    def test_openapi_has_required_paths(self):
        text = Path("specs/openapi.yaml").read_text(encoding="utf-8")
        self.assertIn("/v1/tasks/parse", text)
        self.assertIn("/v1/tasks/{task_id}", text)
        self.assertIn("/v1/tasks", text)
        self.assertIn("/v1/stats/queue", text)
        self.assertIn("sources:", text)
        self.assertIn("artifacts:", text)
        self.assertIn("last_error:", text)

    def test_task_created_schema_shape(self):
        schema = json.loads(Path("specs/events/task.created.v1.schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue({"spec_version", "task_id", "payload"}.issubset(required))

    def test_task_status_schema_shape(self):
        schema = json.loads(Path("specs/events/task.status.v1.schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue({"spec_version", "task_id", "status", "updated_at"}.issubset(required))


if __name__ == "__main__":
    unittest.main()
