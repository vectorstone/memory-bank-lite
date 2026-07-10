import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "mb.py"

class CliTest(unittest.TestCase):
    def test_init_start_recall_use_central_home(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo = root / "repo"; home = root / "memory"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            env = {**os.environ, "MB_LITE_HOME": str(home)}
            result = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init", "--json"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            started = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "start"], env=env, capture_output=True, text=True)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertIn("# Memory Bank", started.stdout)
            self.assertLessEqual(len(started.stdout.encode()), 8192)

    def test_start_uninitialized_returns_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"; subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            result = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "start"], env={**os.environ, "MB_LITE_HOME": str(Path(d)/"m")}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2); self.assertIn("not initialized", result.stderr)

if __name__ == "__main__":
    unittest.main()
