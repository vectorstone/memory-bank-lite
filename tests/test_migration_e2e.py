from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
MIGRATE = REPO / "migrate-cleanup.sh"
MANAGED_PLUGIN = "./.opencode/plugins/memory-bank.js"


class MigrationEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.home = Path(self.temporary_directory.name) / "home"
        self.home.mkdir()
        self.central = self.home / "central"
        self.repo = self.home / "project"
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "remote",
                "add",
                "origin",
                "git@github.com:example/repo.git",
            ],
            check=True,
        )

        self.bank = self.home / "legacy-bank" / ".memory-bank"
        self.bank.mkdir(parents=True)
        for name in ("status.md", "checklist.md", "progress.md"):
            (self.bank / name).write_text(f"# legacy {name}\n", encoding="utf-8")

        self.legacy_registry = self.home / ".codex/memory-bank/registry.json"
        self._write_json(
            self.legacy_registry,
            {"projects": {str(self.repo): {"bank_path": str(self.bank)}}},
        )

        self.global_agents = self.home / ".codex/AGENTS.md"
        self.global_agents.write_text(
            "user-global\n"
            "<!-- memory-bank-codex:start -->\n"
            "managed-global\n"
            "<!-- memory-bank-codex:end -->\n",
            encoding="utf-8",
        )
        self.project_agents = self.repo / "AGENTS.md"
        self.project_agents.write_text(
            "user-project\n"
            "<!-- memory-bank-codex:start -->\n"
            "managed-project\n"
            "<!-- memory-bank-codex:end -->\n",
            encoding="utf-8",
        )

        self.plugin = self.repo / ".opencode/plugins/memory-bank.js"
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_text("managed plugin\n", encoding="utf-8")
        self.adapter_manifest = self.repo / ".opencode/.mb-manifest.json"
        self._write_json(
            self.adapter_manifest,
            {"files": [str(self.plugin)], "plugin_ref": MANAGED_PLUGIN},
        )
        self.opencode = self.repo / "opencode.json"
        self._write_json(
            self.opencode,
            {"plugin": [MANAGED_PLUGIN, "user-plugin"], "theme": "dark"},
        )
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "MB_LITE_HOME": str(self.central),
        }

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(MIGRATE), *arguments],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def _file_bytes(self) -> dict[str, bytes]:
        roots = (self.home, self.repo)
        return {
            str(path): path.read_bytes()
            for root in roots
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }

    def test_dry_run_apply_and_rollback_preserve_user_owned_content(self) -> None:
        before = self._file_bytes()

        dry_run = self._run()

        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn("DRY RUN", dry_run.stdout)
        self.assertIn(str(self.central / "registry.json"), dry_run.stdout)
        for action in (
            "copy-bank",
            "replace-registry",
            "strip-blocks",
            "remove-owned",
            "opencode-plugin",
        ):
            self.assertIn(action, dry_run.stdout)
        self.assertEqual(self._file_bytes(), before)
        self.assertFalse((self.home / ".local/state/mb-migration").exists())

        applied = self._run("--apply", "--timestamp", "migration-e2e")

        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("migration-e2e", applied.stdout)
        project_id = "repo-" + sha256(
            b"github.com/example/repo",
        ).hexdigest()[:12]
        target_bank = self.central / "projects" / project_id / ".memory-bank"
        self.assertEqual(
            (target_bank / "status.md").read_bytes(),
            (self.bank / "status.md").read_bytes(),
        )
        registry = json.loads(
            (self.central / "registry.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            registry["projects"]["github.com/example/repo"]["bank_path"],
            str(target_bank),
        )
        self.assertFalse(self.legacy_registry.exists())
        self.assertEqual(self.global_agents.read_text(encoding="utf-8"), "user-global\n")
        self.assertEqual(self.project_agents.read_text(encoding="utf-8"), "user-project\n")
        self.assertFalse(self.plugin.exists())
        self.assertFalse(self.adapter_manifest.exists())
        self.assertEqual(
            json.loads(self.opencode.read_text(encoding="utf-8")),
            {"plugin": ["user-plugin"], "theme": "dark"},
        )

        rolled_back = self._run("--rollback", "migration-e2e")

        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertFalse((self.central / "registry.json").exists())
        self.assertTrue(target_bank.is_dir())
        for path, content in before.items():
            self.assertEqual(Path(path).read_bytes(), content, path)


if __name__ == "__main__":
    unittest.main()
