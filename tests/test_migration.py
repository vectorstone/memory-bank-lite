from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.migration import legacy_registry_paths, plan_migration, tree_digest


class MigrationTest(unittest.TestCase):
    def test_legacy_registry_paths_cover_three_hosts(self) -> None:
        home = Path("/tmp/home")
        self.assertEqual(
            legacy_registry_paths(home),
            (
                home / ".codex/memory-bank/registry.json",
                home / ".claude/memory-bank/registry.json",
                home / ".config/opencode/memory-bank/registry.json",
            ),
        )

    def test_identical_host_records_merge_to_one_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            bank = root / "shared" / ".memory-bank"
            bank.mkdir(parents=True)
            for name in ("status.md", "checklist.md", "progress.md"):
                (bank / name).write_text(f"# {name}\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            for registry in legacy_registry_paths(root):
                registry.parent.mkdir(parents=True)
                registry.write_text(json.dumps({"projects": {str(repo): {"bank_path": str(bank)}}}), encoding="utf-8")
            plan = plan_migration(root, root / "central")
            self.assertEqual(tuple(plan.projects), ("github.com/example/repo",))
            self.assertEqual(plan.projects["github.com/example/repo"].source_banks, (bank.resolve(),))
            self.assertEqual(plan.projects["github.com/example/repo"].known_roots, (repo.resolve(),))

    def test_different_banks_for_same_remote_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            registries = legacy_registry_paths(root)
            for index, registry in enumerate(registries[:2]):
                bank = root / f"bank-{index}" / ".memory-bank"
                bank.mkdir(parents=True)
                for name in ("status.md", "checklist.md", "progress.md"):
                    (bank / name).write_text(f"{name}-{index}\n", encoding="utf-8")
                registry.parent.mkdir(parents=True)
                registry.write_text(json.dumps({"projects": {str(repo): {"bank_path": str(bank)}}}), encoding="utf-8")
            with self.assertRaisesRegex(MbError, "different Bank content"):
                plan_migration(root, root / "central")

    def test_tree_digest_ignores_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory) / ".memory-bank"
            bank.mkdir(parents=True)
            (bank / "status.md").write_text("# Status\n", encoding="utf-8")
            (bank / ".mb-lite.lock").write_text("", encoding="utf-8")
            first = tree_digest(bank)
            (bank / ".mb-lite.lock").write_text("changed", encoding="utf-8")
            self.assertEqual(tree_digest(bank), first)


if __name__ == "__main__":
    unittest.main()
