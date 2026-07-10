from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.identity import _git, normalize_remote, resolve_identity


def _run_git(*args: str) -> None:
    subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )


class IdentityTest(unittest.TestCase):
    @patch("scripts.mb_lite.identity.subprocess.run")
    def test_git_os_error_is_rejected(self, run) -> None:
        run.side_effect = OSError("git is unavailable")
        with self.assertRaisesRegex(MbError, "unable to run Git") as raised:
            _git(Path("/tmp/repo"), "remote")
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity.subprocess.run")
    def test_required_git_command_failure_is_rejected(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="Git command failed",
        )
        for args in (("remote",), ("remote", "get-url", "origin")):
            with self.subTest(args=args):
                with self.assertRaisesRegex(MbError, "Git command failed") as raised:
                    _git(Path("/tmp/repo"), *args)
                self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity.subprocess.run")
    def test_missing_push_default_returns_empty(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
        )
        self.assertEqual(
            _git(Path("/tmp/repo"), "config", "--get", "remote.pushDefault"),
            "",
        )

    def test_equivalent_remote_forms_have_one_key(self) -> None:
        expected = "github.com/example/onduty"
        self.assertEqual(normalize_remote("git@github.com:example/onduty.git"), expected)
        self.assertEqual(normalize_remote("ssh://git@github.com/example/onduty.git"), expected)
        self.assertEqual(
            normalize_remote("https://token@github.com/example/onduty.git?x=1#f"),
            expected,
        )

    def test_malformed_remote_is_rejected_with_mb_error(self) -> None:
        with self.assertRaisesRegex(MbError, "unsupported Git remote URL") as raised:
            normalize_remote("https://[broken")
        self.assertEqual(raised.exception.exit_code, 1)

    def test_non_default_port_is_preserved(self) -> None:
        self.assertEqual(
            normalize_remote("https://example.com:8443/Org/Repo.git"),
            "example.com:8443/Org/Repo",
        )

    def test_default_port_is_removed(self) -> None:
        self.assertEqual(
            normalize_remote("https://example.com:443/Org/Repo.git"),
            "example.com/Org/Repo",
        )

    def test_malformed_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(MbError, "unsupported Git remote URL") as raised:
            normalize_remote("https://example.com:notaport/Org/Repo.git")
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity._git")
    def test_push_default_wins_over_origin(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "upstream",
            ("remote",): "origin\nupstream",
            ("remote", "get-url", "upstream"): "git@github.com:team/project.git",
        }[args]
        identity = resolve_identity(Path("/tmp/repo"))
        self.assertEqual(identity.key, "github.com/team/project")
        self.assertEqual(identity.project_id, "project-" + identity.digest)

    @patch("scripts.mb_lite.identity._git")
    def test_stale_push_default_is_rejected(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "upstream",
            ("remote",): "origin",
            ("remote", "get-url", "upstream"): "",
        }[args]
        with self.assertRaisesRegex(MbError, "remote.pushDefault") as raised:
            resolve_identity(Path("/tmp/repo"))
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity._git")
    def test_origin_wins_when_push_default_is_absent(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "origin\nupstream",
            ("remote", "get-url", "origin"): "https://github.com/team/project.git",
        }[args]
        self.assertEqual(
            resolve_identity(Path("/tmp/repo")).key,
            "github.com/team/project",
        )

    @patch("scripts.mb_lite.identity._git")
    def test_selected_remote_without_url_is_rejected(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "origin",
            ("remote", "get-url", "origin"): "",
        }[args]
        with self.assertRaisesRegex(MbError, "remote has no URL") as raised:
            resolve_identity(Path("/tmp/repo"))
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity._git")
    def test_only_remote_is_selected_without_origin(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "upstream",
            ("remote", "get-url", "upstream"): "git@github.com:team/project.git",
        }[args]
        self.assertEqual(
            resolve_identity(Path("/tmp/repo")).key,
            "github.com/team/project",
        )

    @patch("scripts.mb_lite.identity._git")
    def test_clones_with_same_remote_share_project_id(self, git) -> None:
        def values(root, *args):
            if args == ("rev-parse", "--show-toplevel"):
                return str(root)
            return {
                ("config", "--get", "remote.pushDefault"): "",
                ("remote",): "origin",
                ("remote", "get-url", "origin"): "git@github.com:team/project.git",
            }[args]

        git.side_effect = values
        first = resolve_identity(Path("/tmp/clone-one"))
        second = resolve_identity(Path("/tmp/clone-two"))
        self.assertEqual(first.project_id, second.project_id)

    def test_linked_worktree_shares_repository_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            linked = root / "linked"
            _run_git("init", "-b", "main", str(repository))
            (repository / "README.md").write_text("identity fixture\n", encoding="utf-8")
            _run_git("-C", str(repository), "add", "README.md")
            _run_git(
                "-C",
                str(repository),
                "-c",
                "user.name=Memory Bank Lite",
                "-c",
                "user.email=memory-bank-lite@example.com",
                "commit",
                "-m",
                "initial",
            )
            _run_git(
                "-C",
                str(repository),
                "remote",
                "add",
                "origin",
                "https://example.com/Org/Repo.git",
            )
            _run_git(
                "-C",
                str(repository),
                "worktree",
                "add",
                "-b",
                "linked",
                str(linked),
            )

            main_identity = resolve_identity(repository)
            linked_identity = resolve_identity(linked)

            self.assertEqual(main_identity.key, "example.com/Org/Repo")
            self.assertEqual(linked_identity.key, main_identity.key)
            self.assertEqual(linked_identity.project_id, main_identity.project_id)

    @patch("scripts.mb_lite.identity._git")
    def test_multiple_remotes_without_origin_are_rejected(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "one\ntwo",
        }[args]
        with self.assertRaisesRegex(MbError, "multiple Git remotes") as raised:
            resolve_identity(Path("/tmp/repo"))
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity._git")
    def test_no_remote_falls_back_to_canonical_root(self, git) -> None:
        reported = Path("/tmp/repo")
        expected = reported.resolve()
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): str(reported),
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "",
        }[args]
        identity = resolve_identity(reported)
        self.assertEqual(identity.root, expected)
        self.assertEqual(identity.key, f"local:{expected}")
        self.assertIsNone(identity.remote)

    @patch("scripts.mb_lite.identity._git")
    def test_no_remote_canonicalizes_repository_root(self, git) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            nested = repository / "nested"
            nested.mkdir()
            reported = nested / ".."
            git.side_effect = lambda root, *args: {
                ("rev-parse", "--show-toplevel"): str(reported),
                ("config", "--get", "remote.pushDefault"): "",
                ("remote",): "",
            }[args]

            identity = resolve_identity(reported)

            expected = repository.resolve()
            self.assertEqual(identity.root, expected)
            self.assertEqual(identity.key, f"local:{expected}")
