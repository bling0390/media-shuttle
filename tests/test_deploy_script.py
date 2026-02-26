import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy.sh"


class TestDeployScript(unittest.TestCase):
    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_dry_run_default_only_core_and_backends(self) -> None:
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("services to deploy: redis mongo core-worker", result.stdout)
        self.assertNotIn(" api ", f" {result.stdout} ")
        self.assertNotIn(" tg ", f" {result.stdout} ")
        self.assertIn("--scale core-worker=1", result.stdout)

    def test_dry_run_with_tg_implies_api(self) -> None:
        result = self.run_script("--dry-run", "--with-tg", "--core-replicas", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("optional services: api=1, tg=1", result.stdout)
        self.assertIn("services to deploy: redis mongo core-worker api tg", result.stdout)
        self.assertIn("--scale core-worker=5", result.stdout)

    def test_invalid_core_replicas(self) -> None:
        result = self.run_script("--dry-run", "--core-replicas", "0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--core-replicas must be an integer >= 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
