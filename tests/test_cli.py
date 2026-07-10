import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "mb.py"

class CliTest(unittest.TestCase):
    def _repo(self, root, remote=None):
        repo = root / "repo"
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        if remote:
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
        return repo

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

    def test_start_section_returns_only_requested_section(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo = self._repo(root); env = {**os.environ, "MB_LITE_HOME": str(root / "m")}
            subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init"], env=env, check=True, capture_output=True)
            result = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "start", "--section", "current"], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0); self.assertIn("## current", result.stdout); self.assertNotIn("## Open Items", result.stdout)

    def test_init_adopt_requires_confirmation_and_reuses_old_bank(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo = self._repo(root, "git@github.com:example/old.git"); home = root / "m"; env = {**os.environ, "MB_LITE_HOME": str(home)}
            old = json.loads(subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init", "--json"], env=env, capture_output=True, text=True, check=True).stdout)
            registry = home / "registry.json"; before = registry.read_bytes()
            subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", "git@github.com:example/new.git"], check=True)
            refused = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init", "--adopt", old["repository_key"]], env=env, capture_output=True, text=True)
            self.assertEqual(refused.returncode, 1); self.assertIn(old["repository_key"], refused.stderr); self.assertIn(old["bank_path"], refused.stderr); self.assertEqual(before, registry.read_bytes())
            adopted = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init", "--adopt", old["repository_key"], "--confirm-adopt", "--json"], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(adopted.stdout)["bank_path"], old["bank_path"])

    def test_recall_json_and_expand(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); repo = self._repo(root); home = root / "m"; env = {**os.environ, "MB_LITE_HOME": str(home)}
            init = json.loads(subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init", "--json"], env=env, capture_output=True, text=True, check=True).stdout)
            note = Path(init["bank_path"]) / "notes" / "query.md"; note.write_text("# Unique Recall\nneedle evidence\n", encoding="utf-8")
            found = json.loads(subprocess.run(["python3", str(CLI), "--project-root", str(repo), "recall", "needle", "--json"], env=env, capture_output=True, text=True, check=True).stdout)
            self.assertEqual(len(found[0]["result_id"]), 12); self.assertEqual(found[0]["path"], "notes/query.md")
            expanded = json.loads(subprocess.run(["python3", str(CLI), "--project-root", str(repo), "recall", "--expand", found[0]["result_id"], "--json"], env=env, capture_output=True, text=True, check=True).stdout)
            self.assertEqual(expanded["heading"], "Unique Recall"); self.assertIn("# Unique Recall", expanded["snippet"])

    def test_done_revision_is_read_only_and_apply_requires_payload(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            home = root / "memory"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            env = {**os.environ, "MB_LITE_HOME": str(home)}
            subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init"], env=env, check=True, capture_output=True)
            revision = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "done", "--revision", "--json"], env=env, capture_output=True, text=True)
            self.assertEqual(revision.returncode, 0, revision.stderr)
            bank = next((home / "projects").glob("*/.memory-bank"))
            before = {path.name: path.read_bytes() for path in bank.iterdir() if path.is_file()}
            self.assertEqual(len(json.loads(revision.stdout)["bank_revision"]), 64)
            after = {path.name: path.read_bytes() for path in bank.iterdir() if path.is_file()}
            self.assertEqual(before, after)
            rejected = subprocess.run(["python3", str(CLI), "--project-root", str(repo), "done", "--apply"], env=env, input="", capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 1)

if __name__ == "__main__":
    unittest.main()
