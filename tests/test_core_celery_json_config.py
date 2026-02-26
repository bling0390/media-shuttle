import unittest
from pathlib import Path


class TestCeleryJsonConfig(unittest.TestCase):
    def test_json_serializer_is_configured(self):
        text = Path("media-shuttle-core/core/queue/celery_app.py").read_text(encoding="utf-8")
        self.assertIn('task_serializer="json"', text)
        self.assertIn('result_serializer="json"', text)
        self.assertIn('accept_content=["json"]', text)


if __name__ == "__main__":
    unittest.main()
