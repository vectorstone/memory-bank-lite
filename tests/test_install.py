import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"
COMMAND = """---
description: Explicit Memory Bank Lite action
---
Load the `mb` skill and execute this action: $ARGUMENTS
Do not run any Memory Bank action implicitly.
"""


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_home.cleanup)
        self.home = Path(self.temporary_home.name)
        self.memory_home = self.home / "memory-bank-lite"
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "MB_LITE_HOME": str(self.memory_home),
            "MB_LITE_SOURCE": str(REPO),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
        }

    def _run(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script)],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_install_creates_only_canonical_skill_links_and_claude_command(self) -> None:
        result = self._run(INSTALL)

        self.assertEqual(result.returncode, 0, result.stderr)
        agents_skill = self.home / ".agents" / "skills" / "mb"
        claude_skill = self.home / ".claude" / "skills" / "mb"
        self.assertTrue(agents_skill.is_symlink())
        self.assertEqual(agents_skill.resolve(), REPO.resolve())
        self.assertTrue(claude_skill.is_symlink())
        self.assertEqual(Path(os.readlink(claude_skill)), agents_skill)
        self.assertEqual(claude_skill.resolve(), REPO.resolve())
        self.assertEqual(
            (self.home / ".claude" / "commands" / "mb.md").read_text(
                encoding="utf-8"
            ),
            COMMAND,
        )
        self.assertFalse((self.home / ".codex" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())
        self.assertFalse(
            (self.home / ".config" / "opencode" / "skills" / "mb").exists()
        )
        self.assertFalse(
            (self.home / ".config" / "opencode" / "commands" / "mb.md").exists()
        )

    def test_install_refuses_to_replace_non_owned_path(self) -> None:
        existing = self.home / ".agents" / "skills" / "mb"
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("user-owned\n", encoding="utf-8")

        result = self._run(INSTALL)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse((self.home / ".claude" / "skills" / "mb").exists())

    def test_install_incomplete_source_writes_nothing_to_home(self) -> None:
        incomplete_source = self.home / "incomplete-source"
        incomplete_source.mkdir()
        self.env["MB_LITE_SOURCE"] = str(incomplete_source)

        result = self._run(INSTALL)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / ".agents" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "commands" / "mb.md").exists())

    def test_install_twice_is_idempotent(self) -> None:
        first = self._run(INSTALL)
        second = self._run(INSTALL)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (self.home / ".agents" / "skills" / "mb").resolve(),
            REPO.resolve(),
        )
        self.assertEqual(
            (self.home / ".claude" / "skills" / "mb").resolve(),
            REPO.resolve(),
        )

    def test_uninstall_preserves_bank_data_and_removes_only_owned_artifacts(self) -> None:
        installed = self._run(INSTALL)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        registry = self.memory_home / "registry.json"
        bank_file = self.memory_home / "projects" / "demo" / ".memory-bank" / "status.md"
        registry.parent.mkdir(parents=True, exist_ok=True)
        bank_file.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text('{"version": 1}\n', encoding="utf-8")
        bank_file.write_text("# Status\n", encoding="utf-8")

        result = self._run(UNINSTALL)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".agents" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "commands" / "mb.md").exists())
        self.assertEqual(registry.read_text(encoding="utf-8"), '{"version": 1}\n')
        self.assertEqual(bank_file.read_text(encoding="utf-8"), "# Status\n")

    def test_uninstall_preserves_non_owned_paths_and_wrapper(self) -> None:
        agents_skill = self.home / ".agents" / "skills" / "mb"
        claude_skill = self.home / ".claude" / "skills" / "mb"
        command = self.home / ".claude" / "commands" / "mb.md"
        other_source = self.home / "other-source"
        other_source.mkdir()
        agents_skill.parent.mkdir(parents=True)
        claude_skill.parent.mkdir(parents=True)
        command.parent.mkdir(parents=True)
        agents_skill.symlink_to(other_source)
        claude_skill.symlink_to(other_source)
        command.write_text("user-owned command\n", encoding="utf-8")

        result = self._run(UNINSTALL)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(agents_skill.resolve(), other_source.resolve())
        self.assertEqual(claude_skill.resolve(), other_source.resolve())
        self.assertEqual(command.read_text(encoding="utf-8"), "user-owned command\n")

    def test_uninstall_preserves_command_symlink_even_with_ownership_text(self) -> None:
        command = self.home / ".claude" / "commands" / "mb.md"
        user_command = self.home / "user-command.md"
        command.parent.mkdir(parents=True)
        user_command.write_text(COMMAND, encoding="utf-8")
        command.symlink_to(user_command)

        result = self._run(UNINSTALL)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(command.is_symlink())
        self.assertEqual(command.resolve(), user_command.resolve())

    def test_uninstall_preserves_modified_owned_wrapper(self) -> None:
        installed = self._run(INSTALL)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        command = self.home / ".claude" / "commands" / "mb.md"
        modified = COMMAND + "User customization\n"
        command.write_text(modified, encoding="utf-8")

        result = self._run(UNINSTALL)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(command.read_text(encoding="utf-8"), modified)

    def test_uninstall_twice_is_idempotent(self) -> None:
        installed = self._run(INSTALL)
        first = self._run(UNINSTALL)
        second = self._run(UNINSTALL)

        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse((self.home / ".agents" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "skills" / "mb").exists())
        self.assertFalse((self.home / ".claude" / "commands" / "mb.md").exists())


if __name__ == "__main__":
    unittest.main()
