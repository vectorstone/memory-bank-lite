import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.mb_lite.bindings import BindingStore
from scripts.mb_lite.catalog import CatalogStore
from scripts.mb_lite.workspace import candidate_members, convert_project_to_workspace
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

    def test_project_conversion_preview_and_apply_preserve_project_identity(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            workspace = self._repo(root, "workspace", "git@github.com:example/container.git")
            self._repo(workspace, "service-a", "git@github.com:example/service-a.git")
            env = {
                **os.environ,
                "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            invalid = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--convert-project"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("requires --workspace", invalid.stderr)
            unbound = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Container", "--convert-project"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(unbound.returncode, 1)
            self.assertIn("exact Project binding", unbound.stderr)
            initialized = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            project = json.loads(initialized.stdout)
            source_bank = Path(project["bank_path"])
            (source_bank / "notes/conversion.md").write_text("preserve this\n", encoding="utf-8")
            catalog_path = vault / "memory-bank/catalog.json"
            bindings_path = root / "state/memory-bank-lite/bindings.json"
            before = (catalog_path.read_bytes(), bindings_path.read_bytes())

            refused = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Container", "--json"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(refused.returncode, 1)
            self.assertIn("--convert-project", refused.stderr)
            previewed = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Container", "--convert-project", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            preview = json.loads(previewed.stdout)
            self.assertEqual(preview["project_conversion"]["source_project_id"], project["scope_id"])
            self.assertEqual(preview["project_conversion"]["source_bank"], str(source_bank))
            self.assertEqual((catalog_path.read_bytes(), bindings_path.read_bytes()), before)

            applied = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Container", "--convert-project", "--confirm-members", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            payload = json.loads(applied.stdout)
            self.assertEqual(payload["source_project_id"], project["scope_id"])
            self.assertTrue(payload["transaction_id"].startswith("workspace-convert-"))
            catalog = CatalogStore(vault, root / "state/memory-bank-lite")
            self.assertEqual(catalog.project(project["scope_id"]).project_id, project["scope_id"])
            record = catalog.workspace(payload["scope_id"])
            self.assertEqual(len(record.members), 1)
            self.assertEqual(BindingStore(root / "state/memory-bank-lite").lookup_exact(workspace).scope_id, record.workspace_id)
            workspace_bank = Path(payload["bank_path"])
            self.assertEqual((workspace_bank / "notes/conversion.md").read_text(encoding="utf-8"), "preserve this\n")
            self.assertEqual((source_bank / "notes/conversion.md").read_text(encoding="utf-8"), "preserve this\n")
            completion = root / "state/memory-bank-lite/transactions" / payload["transaction_id"] / "complete.json"
            self.assertEqual(json.loads(completion.read_text(encoding="utf-8"))["status"], "committed")

    def test_project_conversion_failure_rolls_back_all_created_state(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            workspace = self._repo(root, "workspace", "git@github.com:example/container.git")
            self._repo(workspace, "service-a", "git@github.com:example/service-a.git")
            env = {
                **os.environ,
                "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            initialized = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            source_project_id = json.loads(initialized.stdout)["scope_id"]
            state_root = root / "state/memory-bank-lite"
            catalog = CatalogStore(vault, state_root)
            bindings = BindingStore(state_root)
            candidates = candidate_members(workspace, ())
            before = (catalog.path.read_bytes(), bindings.path.read_bytes())
            with mock.patch.object(bindings, "rebind", side_effect=OSError("injected rebind failure")):
                with self.assertRaisesRegex(OSError, "injected rebind failure"):
                    convert_project_to_workspace(
                        workspace,
                        "Container",
                        tuple(item.root for item in candidates),
                        catalog,
                        bindings,
                        state_root,
                    )
            self.assertEqual((catalog.path.read_bytes(), bindings.path.read_bytes()), before)
            self.assertEqual(bindings.lookup_exact(workspace).scope_id, source_project_id)
            self.assertFalse((vault / "memory-bank/workspaces").exists())
            self.assertEqual(len(list((vault / "memory-bank/projects").iterdir())), 1)
            completions = list((state_root / "transactions").glob("*/complete.json"))
            self.assertEqual(len(completions), 1)
            self.assertEqual(json.loads(completions[0].read_text(encoding="utf-8"))["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
