import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest


class V2WorkspaceTest(unittest.TestCase):
    def _repo(self, root: Path, name: str, remote: str) -> Path:
        repo = root / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
        return repo

    def test_workspace_preview_confirm_start_and_recall(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            workspace = root / "workspace"
            workspace.mkdir()
            first = self._repo(workspace, "service-a", "git@github.com:example/service-a.git")
            second = self._repo(workspace, "service-b", "git@github.com:example/service-b.git")
            env = {
                **os.environ, "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
            }
            preview = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Order Sync", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertTrue(json.loads(preview.stdout)["requires_confirmation"])
            self.assertFalse((vault / "memory-bank/catalog.json").exists())
            applied = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Order Sync", "--confirm-members", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            payload = json.loads(applied.stdout)
            self.assertEqual(len(payload["members"]), 2)
            first_bank = next((vault / "memory-bank/projects").glob("service-a-*/.memory-bank"))
            (first_bank / "notes/evidence.md").write_text("# Contract\n\nshared protocol evidence\n", encoding="utf-8")
            started = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "start"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertIn("## Members", started.stdout)
            self.assertIn("service-a", started.stdout)
            recalled = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "recall", "shared protocol", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            results = json.loads(recalled.stdout)
            self.assertEqual(results[0]["layer"], "bank")
            self.assertIn("service-a-", results[0]["scope_id"])

    def test_same_remote_worktrees_are_one_member(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            workspace = root / "workspace"
            workspace.mkdir()
            first = self._repo(workspace, "one", "git@github.com:example/shared.git")
            second = self._repo(workspace, "two", "https://github.com/example/shared.git")
            env = {**os.environ, "MB_LITE_VAULT_PATH": str(vault), "XDG_STATE_HOME": str(root / "state")}
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Shared", "--confirm-members", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(len(json.loads(result.stdout)["members"]), 1)


if __name__ == "__main__":
    unittest.main()
