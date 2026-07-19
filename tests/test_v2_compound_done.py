import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.mb_lite import compound as compound_module
from scripts.mb_lite.compound import CompoundUpdate, apply_compound_done, global_lock
from scripts.mb_lite.done import BankWriteError, DonePayload
from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest


def done_update(scope_id: str, revision: str, summary: str) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "bank_revision": revision,
        "summary": summary,
        "decisions": [], "next_steps": [], "blockers": [], "key_files": [],
        "completed_items": [], "new_items": [], "note": None,
        "attention_changes": [],
    }


class V2CompoundDoneTest(unittest.TestCase):
    def _repo(self, root: Path, name: str) -> Path:
        repo = root / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", f"git@github.com:example/{name}.git"], check=True)
        return repo

    def _fixture(self, root: Path):
        vault = V2ConfigCatalogTest()._vault(root)
        workspace = root / "workspace"
        workspace.mkdir()
        self._repo(workspace, "service-a")
        self._repo(workspace, "service-b")
        env = {
            **os.environ, "MB_LITE_VAULT_PATH": str(vault),
            "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
        }
        initialized = subprocess.run(
            ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Order Sync", "--confirm-members", "--json"],
            env=env, capture_output=True, text=True, check=True,
        )
        return vault, workspace, env, json.loads(initialized.stdout)

    def test_workspace_done_updates_workspace_and_selected_project_once(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, workspace, env, initialized = self._fixture(root)
            revision = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            snapshot = json.loads(revision.stdout)
            workspace_bank = snapshot["banks"][0]
            project_bank = snapshot["banks"][1]
            payload = {
                "workspace_id": initialized["scope_id"],
                "catalog_revision": snapshot["catalog_revision"],
                "updates": [
                    done_update(workspace_bank["scope_id"], workspace_bank["bank_revision"], "Integrated services"),
                    done_update(project_bank["scope_id"], project_bank["bank_revision"], "Updated service contract"),
                ],
            }
            applied = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            result = json.loads(applied.stdout)
            self.assertEqual(set(result["revisions"]), {workspace_bank["scope_id"], project_bank["scope_id"]})
            closeout_path = vault / "memory-bank/closeout-state" / f"{initialized['scope_id']}.json"
            self.assertTrue(closeout_path.is_file())
            closeout_text = closeout_path.read_text(encoding="utf-8")
            self.assertNotIn(str(workspace), closeout_text)
            next_snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            self.assertEqual(next_snapshot["closeout_state"]["transaction_id"], result["transaction_id"])
            other = Path(snapshot["banks"][2]["bank_path"]) / "status.md"
            self.assertIn("**Focus:** Not set", other.read_text(encoding="utf-8"))

    def test_stale_member_revision_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, workspace, env, initialized = self._fixture(root)
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            workspace_bank, project_bank = snapshot["banks"][:2]
            workspace_status = Path(workspace_bank["bank_path"]) / "status.md"
            before = workspace_status.read_bytes()
            payload = {
                "workspace_id": initialized["scope_id"], "catalog_revision": snapshot["catalog_revision"],
                "updates": [
                    done_update(workspace_bank["scope_id"], workspace_bank["bank_revision"], "Must not land"),
                    done_update(project_bank["scope_id"], "0" * 64, "Stale"),
                ],
            }
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 3)
            self.assertEqual(workspace_status.read_bytes(), before)

    def test_sensitive_payload_is_blocked_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, workspace, env, initialized = self._fixture(root)
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            bank = snapshot["banks"][0]
            update = done_update(bank["scope_id"], bank["bank_revision"], "Leaked sk-abcdefghijklmnop")
            payload = {"workspace_id": initialized["scope_id"], "catalog_revision": snapshot["catalog_revision"], "updates": [update]}
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--apply"],
                env=env, input=json.dumps(payload), capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sensitive content blocked", result.stderr)

    def test_incomplete_transaction_blocks_later_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, workspace, env, initialized = self._fixture(root)
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            bank = snapshot["banks"][0]
            status = Path(bank["bank_path"]) / "status.md"
            before = status.read_bytes()
            incomplete = Path(env["XDG_STATE_HOME"]) / "memory-bank-lite/transactions/interrupted"
            incomplete.mkdir(parents=True)
            payload = {
                "workspace_id": initialized["scope_id"], "catalog_revision": snapshot["catalog_revision"],
                "updates": [done_update(bank["scope_id"], bank["bank_revision"], "Must not write")],
            }
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--apply"],
                env=env, input=json.dumps(payload), capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 4)
            self.assertIn("incomplete transaction blocks writes", result.stderr)
            self.assertEqual(status.read_bytes(), before)

    def test_forbidden_secret_file_is_not_accepted_as_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, workspace, env, initialized = self._fixture(root)
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            bank = snapshot["banks"][0]
            update = done_update(bank["scope_id"], bank["bank_revision"], "Unsafe evidence")
            update["key_files"] = [".env"]
            payload = {
                "workspace_id": initialized["scope_id"], "catalog_revision": snapshot["catalog_revision"],
                "updates": [update],
            }
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--apply"],
                env=env, input=json.dumps(payload), capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("forbidden sensitive input", result.stderr)

    def test_successful_compound_rollback_does_not_block_later_writes(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, workspace, env, initialized = self._fixture(root)
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            bank = snapshot["banks"][0]
            raw_update = done_update(bank["scope_id"], bank["bank_revision"], "Rolled back")
            update = CompoundUpdate(
                bank["scope_id"], Path(bank["bank_path"]), workspace, DonePayload.from_json(json.dumps(raw_update))
            )
            state_root = Path(env["XDG_STATE_HOME"]) / "memory-bank-lite"
            with mock.patch.object(
                compound_module,
                "_write_all_or_rollback",
                side_effect=BankWriteError("injected write failure", True),
            ):
                with self.assertRaisesRegex(BankWriteError, "injected write failure"):
                    apply_compound_done((update,), state_root, json.dumps(raw_update))
            completions = list((state_root / "transactions").glob("*/complete.json"))
            self.assertEqual(len(completions), 1)
            self.assertEqual(json.loads(completions[0].read_text(encoding="utf-8"))["status"], "rolled_back")
            with global_lock(state_root):
                pass


if __name__ == "__main__":
    unittest.main()
