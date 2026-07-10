from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.identity import normalize_remote, resolve_identity


class IdentityTest(unittest.TestCase):
    def test_equivalent_remote_forms_have_one_key(self) -> None:
        expected = "github.com/example/onduty"
        self.assertEqual(normalize_remote("git@github.com:example/onduty.git"), expected)
        self.assertEqual(normalize_remote("ssh://git@github.com/example/onduty.git"), expected)
        self.assertEqual(
            normalize_remote("https://token@github.com/example/onduty.git?x=1#f"),
            expected,
        )

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
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "",
        }[args]
        identity = resolve_identity(Path("/tmp/repo"))
        self.assertEqual(identity.key, "local:/tmp/repo")
        self.assertIsNone(identity.remote)
