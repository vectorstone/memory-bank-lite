from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
MIGRATE = REPO / "migrate-cleanup.sh"
MANAGED_PLUGIN = "./.opencode/plugins/memory-bank.js"
sys.path.insert(0, str(REPO / "scripts"))

from mb_lite.errors import MbError  # noqa: E402
from mb_lite.migration import plan_migration  # noqa: E402
from migrate_cleanup import _apply, _cleanup_actions, _copy_banks  # noqa: E402


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

    def _target_bank(self) -> Path:
        project_id = "repo-" + sha256(
            b"github.com/example/repo",
        ).hexdigest()[:12]
        return self.central / "projects" / project_id / ".memory-bank"

    def _copy_bank(self, target: Path, prefix: str = "legacy") -> None:
        target.mkdir(parents=True)
        for name in ("status.md", "checklist.md", "progress.md"):
            (target / name).write_text(f"# {prefix} {name}\n", encoding="utf-8")

    def _write_central_registry(self, projects: dict[str, object]) -> None:
        self._write_json(
            self.central / "registry.json",
            {
                "version": 1,
                "projects_root": str(self.central.resolve() / "projects"),
                "projects": projects,
            },
        )

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
        target_bank = self._target_bank()
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

    def test_mixed_registry_migrates_valid_record_and_retains_unresolved_record(self) -> None:
        missing_root = self.home / "removed-project"
        unresolved_bank_target = self.home / "unresolved-bank-target" / ".memory-bank"
        self._copy_bank(unresolved_bank_target, prefix="unresolved")
        unresolved_bank = self.home / "unresolved-bank-link" / ".memory-bank"
        unresolved_bank.parent.mkdir()
        unresolved_bank.symlink_to(unresolved_bank_target, target_is_directory=True)
        unresolved_bank_anchored = unresolved_bank.parent.resolve() / unresolved_bank.name
        nested = unresolved_bank / "notes" / "evidence.bin"
        nested.parent.mkdir()
        nested.write_bytes(b"unresolved-bank-bytes\x00\xff")
        outside_target = self.home / "outside-target.bin"
        outside_target.write_bytes(b"outside-target-bytes\x00\xff")
        escaping_link = nested.parent / "outside-link"
        escaping_link.symlink_to(outside_target)
        escaping_link_anchored = escaping_link.parent.resolve() / escaping_link.name
        unrelated_owned = self.home / ".claude/agents/unrelated-owned.md"
        unrelated_owned.parent.mkdir(parents=True)
        unrelated_owned.write_text("managed unrelated artifact\n", encoding="utf-8")
        self._write_json(
            self.legacy_registry,
            {
                "projects": {
                    str(self.repo): {"bank_path": str(self.bank)},
                    str(missing_root): {"bank_path": str(unresolved_bank)},
                },
            },
        )
        legacy_manifest = self.home / "legacy-install.json"
        self._write_json(
            legacy_manifest,
            {
                "files": [
                    str(self.legacy_registry),
                    str(unresolved_bank),
                    str(unresolved_bank / "status.md"),
                    str(nested),
                    str(escaping_link),
                    str(unrelated_owned),
                ],
            },
        )
        self.env["MB_LEGACY_MANIFEST"] = str(legacy_manifest)
        registry_before = self.legacy_registry.read_bytes()
        unresolved_bank_link_before = unresolved_bank.readlink()
        outside_target_before = outside_target.read_bytes()
        unresolved_before = {
            path.relative_to(unresolved_bank_target): path.read_bytes()
            for path in unresolved_bank_target.rglob("*")
            if path.is_file()
        }
        before = self._file_bytes()

        first_dry_run = self._run()
        second_dry_run = self._run()

        self.assertEqual(first_dry_run.returncode, 0, first_dry_run.stderr)
        self.assertEqual(second_dry_run.returncode, 0, second_dry_run.stderr)
        self.assertEqual(first_dry_run.stdout, second_dry_run.stdout)
        self.assertIn("Unresolved legacy records", first_dry_run.stdout)
        self.assertIn(str(self.legacy_registry), first_dry_run.stdout)
        self.assertIn(str(missing_root), first_dry_run.stdout)
        self.assertIn(str(unresolved_bank), first_dry_run.stdout)
        self.assertIn("registry retained", first_dry_run.stdout)
        self.assertNotIn(
            f"remove-owned: {self.legacy_registry.resolve()}",
            first_dry_run.stdout,
        )
        self.assertNotIn(
            f"remove-owned: {unresolved_bank_anchored}",
            first_dry_run.stdout,
        )
        self.assertNotIn(
            f"remove-owned: {(unresolved_bank / 'status.md').resolve()}",
            first_dry_run.stdout,
        )
        self.assertNotIn(
            f"remove-owned: {nested.resolve()}",
            first_dry_run.stdout,
        )
        self.assertNotIn(
            f"remove-owned: {escaping_link_anchored}",
            first_dry_run.stdout,
        )
        self.assertIn(
            f"remove-owned: {unrelated_owned.resolve()}",
            first_dry_run.stdout,
        )
        self.assertEqual(self._file_bytes(), before)
        self.assertTrue(unresolved_bank.is_symlink())
        self.assertEqual(unresolved_bank.readlink(), unresolved_bank_link_before)
        self.assertFalse((self.home / ".local/state/mb-migration").exists())

        applied = self._run("--apply", "--timestamp", "mixed-registry")

        self.assertEqual(applied.returncode, 0, applied.stderr)
        target_bank = self._target_bank()
        self.assertEqual(
            (target_bank / "status.md").read_bytes(),
            (self.bank / "status.md").read_bytes(),
        )
        projects = json.loads(
            (self.central / "registry.json").read_text(encoding="utf-8"),
        )["projects"]
        self.assertEqual(tuple(projects), ("github.com/example/repo",))
        self.assertEqual(self.legacy_registry.read_bytes(), registry_before)
        self.assertFalse(unrelated_owned.exists())
        self.assertTrue(unresolved_bank.is_symlink())
        self.assertEqual(unresolved_bank.readlink(), unresolved_bank_link_before)
        self.assertTrue(escaping_link.is_symlink())
        self.assertEqual(escaping_link.readlink(), outside_target)
        self.assertEqual(outside_target.read_bytes(), outside_target_before)
        self.assertEqual(
            {
                path.relative_to(unresolved_bank_target): path.read_bytes()
                for path in unresolved_bank_target.rglob("*")
                if path.is_file()
            },
            unresolved_before,
        )

        rolled_back = self._run("--rollback", "mixed-registry")

        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertFalse((self.central / "registry.json").exists())
        self.assertTrue(target_bank.is_dir())
        self.assertEqual(self.legacy_registry.read_bytes(), registry_before)
        self.assertTrue(unresolved_bank.is_symlink())
        self.assertEqual(unresolved_bank.readlink(), unresolved_bank_link_before)
        self.assertTrue(escaping_link.is_symlink())
        self.assertEqual(escaping_link.readlink(), outside_target)
        self.assertEqual(outside_target.read_bytes(), outside_target_before)
        self.assertEqual(
            {
                path.relative_to(unresolved_bank_target): path.read_bytes()
                for path in unresolved_bank_target.rglob("*")
                if path.is_file()
            },
            unresolved_before,
        )
        for path, content in before.items():
            self.assertEqual(Path(path).read_bytes(), content, path)

    def test_apply_unions_existing_roots_and_preserves_unrelated_projects(self) -> None:
        target_bank = self._target_bank()
        self._copy_bank(target_bank)
        unrelated = {
            "project_id": "other-123",
            "remote": "github.com/example/other",
            "bank_path": str(self.central / "projects/other-123/.memory-bank"),
            "known_roots": ["/existing/other"],
        }
        self._write_central_registry(
            {
                "github.com/example/repo": {
                    "project_id": target_bank.parent.name,
                    "remote": "github.com/example/repo",
                    "bank_path": str(target_bank),
                    "known_roots": ["/existing/root"],
                },
                "github.com/example/other": unrelated,
            },
        )

        result = self._run("--apply", "--timestamp", "merge-roots")

        self.assertEqual(result.returncode, 0, result.stderr)
        projects = json.loads(
            (self.central / "registry.json").read_text(encoding="utf-8"),
        )["projects"]
        self.assertEqual(projects["github.com/example/other"], unrelated)
        self.assertEqual(
            projects["github.com/example/repo"]["known_roots"],
            sorted(("/existing/root", str(self.repo.resolve()))),
        )

    def test_existing_same_key_with_different_bank_fails_before_mutation(self) -> None:
        existing_bank = self.central / "projects/existing/.memory-bank"
        self._copy_bank(existing_bank, prefix="different")
        existing_entry = {
            "project_id": "existing",
            "remote": "github.com/example/repo",
            "bank_path": str(existing_bank),
            "known_roots": ["/existing/root"],
        }
        self._write_central_registry(
            {"github.com/example/repo": existing_entry},
        )
        registry_before = (self.central / "registry.json").read_bytes()
        agents_before = self.global_agents.read_bytes()

        result = self._run("--apply", "--timestamp", "bank-conflict")

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertNotIn("successfully", result.stdout)
        self.assertEqual((self.central / "registry.json").read_bytes(), registry_before)
        self.assertEqual(self.global_agents.read_bytes(), agents_before)
        self.assertTrue(self.legacy_registry.is_file())
        self.assertTrue(self.plugin.is_file())
        self.assertFalse(self._target_bank().exists())

    def test_symlinked_project_parent_cannot_escape_central_projects_root(self) -> None:
        target = self._target_bank()
        outside = self.home / "outside"
        outside.mkdir()
        target.parent.parent.mkdir(parents=True)
        target.parent.symlink_to(outside, target_is_directory=True)
        agents_before = self.global_agents.read_bytes()

        result = self._run("--apply", "--timestamp", "bank-escape")

        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(self.global_agents.read_bytes(), agents_before)
        self.assertTrue(self.legacy_registry.is_file())
        self.assertFalse((outside / ".memory-bank").exists())

    def test_failed_bank_copy_removes_temporary_and_never_creates_final(self) -> None:
        plan = plan_migration(self.home, self.central)
        target = self._target_bank()

        def fail_after_partial_copy(
            _source: Path,
            destination: int,
        ) -> None:
            os.mkdir(".memory-bank", dir_fd=destination)
            partial = os.open(
                "partial",
                os.O_WRONLY | os.O_CREAT,
                dir_fd=destination,
            )
            os.close(partial)
            raise OSError("injected copy failure")

        with patch(
            "migrate_cleanup._copy_bank_to_project",
            side_effect=fail_after_partial_copy,
        ):
            with self.assertRaisesRegex(MbError, "unable to copy Bank"):
                _copy_banks(plan)

        self.assertFalse(target.exists())
        self.assertEqual(
            list(target.parent.parent.glob(".mb-copy-*")),
            [],
        )

    def test_bank_final_placement_symlink_race_cannot_write_outside(self) -> None:
        plan = plan_migration(self.home, self.central)
        target = self._target_bank()
        outside = self.home / "outside-race"
        outside.mkdir()
        real_rename = os.rename

        def create_symlink_before_rename(
            source: str,
            destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
        ) -> None:
            os.symlink(str(outside), destination, dir_fd=dst_dir_fd)
            real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        with patch(
            "migrate_cleanup.os.rename",
            autospec=True,
            side_effect=create_symlink_before_rename,
        ) as rename:
            with self.assertRaisesRegex(MbError, "unable to copy Bank") as raised:
                _copy_banks(plan)

        self.assertEqual(rename.call_count, 1, raised.exception)
        self.assertFalse((outside / ".memory-bank").exists())
        self.assertFalse(target.exists())

    def test_transaction_manifest_is_durable_before_registry_mutation(self) -> None:
        plan = plan_migration(self.home, self.central)
        actions = _cleanup_actions(self.home, plan)
        state = self.home / ".local/state/mb-migration"
        manifest = state / "pre-capture/manifest.json"
        observed_paths: list[set[str]] = []

        def fail_after_inspecting_manifest(
            _store: object,
            _projects: object,
            *,
            expected_projects: object,
        ) -> None:
            self.assertIsNotNone(expected_projects)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            observed_paths.append({str(record["path"]) for record in data["records"]})
            raise MbError("injected registry failure", 4)

        with patch(
            "migrate_cleanup.RegistryStore.replace_projects",
            autospec=True,
            side_effect=fail_after_inspecting_manifest,
        ):
            with self.assertRaisesRegex(MbError, "injected registry failure"):
                _apply(plan, self.central, actions, "pre-capture", state)

        self.assertEqual(len(observed_paths), 1)
        observed_resolved = {str(Path(path).resolve()) for path in observed_paths[0]}
        expected_paths = {
            self.central / "registry.json",
            self.legacy_registry,
            self.global_agents,
            self.project_agents,
            self.plugin,
            self.adapter_manifest,
            self.opencode,
        }
        self.assertTrue(
            {str(path.resolve()) for path in expected_paths}.issubset(
                observed_resolved,
            ),
            observed_resolved,
        )

    def test_legacy_manifest_protects_exact_shared_paths_only(self) -> None:
        rules = self.home / ".claude/RULES.md"
        hooks = self.home / ".claude/hooks.json"
        owned_agent = self.home / ".claude/agents/mb-rules-enforcer.md"
        rules.parent.mkdir(parents=True, exist_ok=True)
        owned_agent.parent.mkdir(parents=True, exist_ok=True)
        rules.write_text("user rules\n", encoding="utf-8")
        hooks.write_text('{"user": true}\n', encoding="utf-8")
        owned_agent.write_text("managed agent\n", encoding="utf-8")
        legacy_manifest = self.home / "legacy-install.json"
        self._write_json(
            legacy_manifest,
            {
                "files": [
                    str(rules),
                    str(hooks),
                    str(self.global_agents),
                    str(owned_agent),
                ],
            },
        )
        self.env["MB_LEGACY_MANIFEST"] = str(legacy_manifest)

        dry_run = self._run()
        result = self._run("--apply", "--timestamp", "protect-shared")

        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn(f"remove-owned: {owned_agent.resolve()}", dry_run.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rules.read_text(encoding="utf-8"), "user rules\n")
        self.assertEqual(hooks.read_text(encoding="utf-8"), '{"user": true}\n')
        self.assertEqual(self.global_agents.read_text(encoding="utf-8"), "user-global\n")
        self.assertFalse(owned_agent.exists())

    def test_invalid_cli_combination_uses_mb_error_exit_one(self) -> None:
        result = self._run("--apply", "--rollback", "migration-e2e")

        self.assertEqual(result.returncode, 1)
        self.assertIn("mb migration:", result.stderr)
        self.assertNotIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
