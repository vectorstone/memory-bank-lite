import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.bindings import BindingStore
from scripts.mb_lite.catalog import CatalogStore, generated_id, remote_project_id
from scripts.mb_lite.config import resolve_runtime, write_vault_config
from scripts.mb_lite.errors import MbError
from scripts.mb_lite.vault import REQUIRED_FILES, validate_vault_contract


class V2ConfigCatalogTest(unittest.TestCase):
    def _vault(self, root: Path) -> Path:
        vault = root / "vault"
        for relative in REQUIRED_FILES:
            path = vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == ".vault-meta/transport.json":
                path.write_text('{"fallback_chain":["filesystem"],"preferred":"filesystem"}\n', encoding="utf-8")
            elif relative == "scripts/wiki-lock.sh":
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)
            elif relative == "scripts/wiki-mode.py":
                path.write_text(
                    '#!/usr/bin/env python3\nimport sys\nprint("generic" if sys.argv[1] == "get" else "wiki/sources/test.md")\n',
                    encoding="utf-8",
                )
                path.chmod(0o755)
            elif relative == "wiki/index.md":
                path.write_text(
                    '---\ntype: meta\ntitle: "Wiki Index"\nupdated: 2026-07-19\n---\n# Wiki Index\n\n## Sources\n',
                    encoding="utf-8",
                )
            elif relative == "wiki/sources/_index.md":
                path.write_text(
                    '---\ntype: meta\ntitle: "Sources Index"\nupdated: 2026-07-19\n---\n# Sources\n\n## By Type\n',
                    encoding="utf-8",
                )
            elif relative == "wiki/log.md":
                path.write_text(
                    '---\ntype: meta\ntitle: "Wiki Log"\nupdated: 2026-07-19\n---\n# Wiki Log\n',
                    encoding="utf-8",
                )
            elif relative == "wiki/hot.md":
                path.write_text(
                    '---\ntype: meta\ntitle: "Hot Cache"\nupdated: 2026-07-19\n---\n# Recent Context\n',
                    encoding="utf-8",
                )
            else:
                path.write_text("# fixture\n", encoding="utf-8")
        return vault

    def test_config_environment_overrides_persistent_vault(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            first = self._vault(root / "first")
            second = self._vault(root / "second")
            env = {"XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")}
            write_vault_config(first, env)
            self.assertEqual(resolve_runtime(env).vault, first.resolve())
            overridden = {**env, "MB_LITE_VAULT_PATH": str(second)}
            self.assertEqual(resolve_runtime(overridden).vault, second.resolve())

    def test_unconfigured_runtime_fails_actionably(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            with self.assertRaisesRegex(MbError, "config set-vault"):
                resolve_runtime({"XDG_CONFIG_HOME": str(root / "c"), "XDG_STATE_HOME": str(root / "s")})

    def test_vault_contract_rejects_missing_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            vault = self._vault(Path(value))
            (vault / "WIKI.md").unlink()
            with self.assertRaisesRegex(MbError, "missing WIKI.md"):
                validate_vault_contract(vault)

    def test_catalog_rejects_memory_bank_symlink_inside_vault(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            vault = self._vault(Path(value))
            (vault / "memory-bank").symlink_to(vault / "wiki", target_is_directory=True)
            store = CatalogStore(vault)
            project_id = remote_project_id("service", "github.com/example/service")
            with self.assertRaisesRegex(MbError, "must not traverse a symlink"):
                store.add_project(project_id, "service", ("github.com/example/service",))

    def test_catalog_contains_only_relative_paths_and_rejects_duplicate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            vault = self._vault(Path(value))
            store = CatalogStore(vault)
            key = "github.com/example/service"
            project_id = remote_project_id("service", key)
            record = store.add_project(project_id, "service", (key,))
            self.assertEqual(record.bank_path, f"memory-bank/projects/{project_id}/.memory-bank")
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn(str(vault), raw)
            with self.assertRaisesRegex(MbError, "already registered"):
                store.add_project(generated_id("other"), "other", (key,))

    def test_catalog_workspace_requires_existing_members(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            vault = self._vault(Path(value))
            store = CatalogStore(vault)
            with self.assertRaisesRegex(MbError, "member is not initialized"):
                store.add_workspace(generated_id("ws"), "ws", (generated_id("missing"),))

    def test_bindings_are_device_local_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            repo = root / "repo"
            repo.mkdir()
            store = BindingStore(root / "state")
            project_id = generated_id("repo")
            store.bind(repo, "project", project_id)
            self.assertEqual(store.lookup_exact(repo).scope_id, project_id)
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertIn(str(repo.resolve()), payload["roots"])


if __name__ == "__main__":
    unittest.main()
