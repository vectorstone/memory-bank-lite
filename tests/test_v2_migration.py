import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.mb_lite.bank import initialize_bank
from scripts.mb_lite.bindings import BindingStore
from scripts.mb_lite.catalog import CatalogStore
from scripts.mb_lite.compound import global_lock
from scripts.mb_lite.migrate_v2 import apply_v2_migration, plan_v2_migration
from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest


class V2MigrationTest(unittest.TestCase):
    def _write_registry(self, legacy: Path, bank: Path, roots: list[str] | None = None) -> None:
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "registry.json").write_text(json.dumps({
            "version": 1,
            "projects_root": str(legacy / "projects"),
            "projects": {
                "github.com/example/repo": {
                    "project_id": "repo-0432dc9db558",
                    "remote": "github.com/example/repo",
                    "bank_path": str(bank),
                    "known_roots": roots or [],
                }
            },
        }), encoding="utf-8")

    def test_migration_rejects_legacy_bank_symlink_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            legacy = root / "legacy"
            bank = legacy / "projects/repo/.memory-bank"
            initialize_bank(bank)
            (bank / "attention.md").unlink()
            outside = root / "outside.txt"
            outside.write_text("outside secret\n", encoding="utf-8")
            (bank / "notes/leak.md").symlink_to(outside)
            (legacy / "registry.json").write_text(json.dumps({
                "version": 1,
                "projects_root": str(legacy / "projects"),
                "projects": {
                    "github.com/example/repo": {
                        "project_id": "repo-0432dc9db558",
                        "remote": "github.com/example/repo",
                        "bank_path": str(bank),
                        "known_roots": [],
                    }
                },
            }), encoding="utf-8")
            env = {
                **os.environ, "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
            }
            result = subprocess.run(
                ["python3", str(CLI), "migrate", "--to-vault", "--legacy-home", str(legacy), "--json"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 4)
            self.assertIn("legacy Bank contains symlinks", result.stderr)
            self.assertFalse((vault / "memory-bank/catalog.json").exists())

    def test_apply_failure_rolls_back_v2_outputs_and_remains_writable(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            legacy = root / "legacy"
            bank = legacy / "projects/repo/.memory-bank"
            initialize_bank(bank)
            (bank / "attention.md").unlink()
            (legacy / "registry.json").write_text(json.dumps({
                "version": 1,
                "projects_root": str(legacy / "projects"),
                "projects": {
                    "github.com/example/repo": {
                        "project_id": "repo-0432dc9db558",
                        "remote": "github.com/example/repo",
                        "bank_path": str(bank),
                        "known_roots": [str(repo)],
                    }
                },
            }), encoding="utf-8")
            state_root = root / "state/memory-bank-lite"
            config_path = root / "config/memory-bank-lite/config.json"
            catalog = CatalogStore(vault, state_root)
            bindings = BindingStore(state_root)
            items = plan_v2_migration(legacy, catalog)
            with mock.patch.object(bindings, "bind", side_effect=OSError("injected bindings failure")):
                with self.assertRaisesRegex(OSError, "injected bindings failure"):
                    apply_v2_migration(items, catalog, bindings, state_root, config_path)
            target = vault / "memory-bank/projects/repo-0432dc9db558/.memory-bank"
            self.assertFalse(target.exists())
            self.assertFalse(catalog.path.exists())
            self.assertFalse(bindings.path.exists())
            self.assertFalse(config_path.exists())
            self.assertTrue(bank.is_dir())
            completions = list((state_root / "transactions").glob("*/complete.json"))
            self.assertEqual(len(completions), 1)
            self.assertEqual(json.loads(completions[0].read_text(encoding="utf-8"))["status"], "rolled_back")
            with global_lock(state_root):
                pass

    def test_dry_run_then_apply_copies_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            legacy = root / "legacy"
            bank = legacy / "projects/repo/.memory-bank"
            initialize_bank(bank)
            # Simulate a v1 Bank by removing the v2-only file.
            (bank / "attention.md").unlink()
            legacy.mkdir(exist_ok=True)
            (legacy / "registry.json").write_text(json.dumps({
                "version": 1,
                "projects_root": str(legacy / "projects"),
                "projects": {
                    "github.com/example/repo": {
                        "project_id": "repo-0432dc9db558",
                        "remote": "github.com/example/repo",
                        "bank_path": str(bank),
                        "known_roots": [str(repo)],
                    }
                },
            }), encoding="utf-8")
            env = {
                **os.environ, "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
            }
            dry = subprocess.run(
                ["python3", str(CLI), "migrate", "--to-vault", "--legacy-home", str(legacy), "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(dry.stdout)["deletes"], [])
            self.assertFalse((vault / "memory-bank/catalog.json").exists())
            applied = subprocess.run(
                ["python3", str(CLI), "migrate", "--to-vault", "--legacy-home", str(legacy), "--apply", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertTrue(json.loads(applied.stdout)["source_preserved"])
            self.assertTrue(bank.is_dir())
            target = vault / "memory-bank/projects/repo-0432dc9db558/.memory-bank"
            self.assertTrue((target / "attention.md").is_file())
            cleanup = subprocess.run(
                ["python3", str(CLI), "migrate", "--cleanup", "--legacy-home", str(legacy), "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(cleanup.stdout)["mode"], "cleanup-dry-run")
            self.assertTrue(legacy.exists())
            cleaned = subprocess.run(
                ["python3", str(CLI), "migrate", "--cleanup", "--legacy-home", str(legacy), "--apply", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            cleaned_payload = json.loads(cleaned.stdout)
            self.assertTrue(cleaned_payload["legacy_home_removed"])
            self.assertTrue(Path(cleaned_payload["recovery_backup"]).is_dir())

    def test_in_place_v1_target_is_upgraded_only_on_apply(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            legacy = root / "legacy"
            bank = vault / "memory-bank/projects/repo-0432dc9db558/.memory-bank"
            initialize_bank(bank)
            (bank / "attention.md").unlink()
            status = (bank / "status.md").read_bytes()
            self._write_registry(legacy, bank)
            state_root = root / "state/memory-bank-lite"
            catalog = CatalogStore(vault, state_root)
            bindings = BindingStore(state_root)
            items = plan_v2_migration(legacy, catalog)
            self.assertFalse((bank / "attention.md").exists())
            result = apply_v2_migration(items, catalog, bindings, state_root, root / "config.json")
            self.assertTrue((bank / "attention.md").is_file())
            self.assertEqual((bank / "status.md").read_bytes(), status)
            self.assertTrue(result["source_preserved"])
            self.assertEqual(catalog.project("repo-0432dc9db558").project_id, "repo-0432dc9db558")

    def test_in_place_upgrade_is_removed_when_later_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = root / "repo"
            repo.mkdir()
            legacy = root / "legacy"
            bank = vault / "memory-bank/projects/repo-0432dc9db558/.memory-bank"
            initialize_bank(bank)
            (bank / "attention.md").unlink()
            status = (bank / "status.md").read_bytes()
            self._write_registry(legacy, bank, [str(repo)])
            state_root = root / "state/memory-bank-lite"
            config_path = root / "config.json"
            catalog = CatalogStore(vault, state_root)
            bindings = BindingStore(state_root)
            items = plan_v2_migration(legacy, catalog)
            with mock.patch.object(bindings, "bind", side_effect=OSError("injected bindings failure")):
                with self.assertRaisesRegex(OSError, "injected bindings failure"):
                    apply_v2_migration(items, catalog, bindings, state_root, config_path)
            self.assertFalse((bank / "attention.md").exists())
            self.assertEqual((bank / "status.md").read_bytes(), status)
            self.assertFalse(catalog.path.exists())
            self.assertFalse(bindings.path.exists())
            self.assertFalse(config_path.exists())
            completions = list((state_root / "transactions").glob("*/complete.json"))
            self.assertEqual(len(completions), 1)
            self.assertEqual(json.loads(completions[0].read_text(encoding="utf-8"))["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
