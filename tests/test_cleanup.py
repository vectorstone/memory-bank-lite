from pathlib import Path
import json
import tempfile
import unittest

from scripts.mb_lite.cleanup import (
    clean_hooks_json,
    manifest_owned_files,
    remove_opencode_plugin,
    strip_known_blocks,
)
from scripts.mb_lite.errors import MbError


class CleanupTest(unittest.TestCase):
    def test_strip_known_blocks_preserves_user_prefix_and_suffix(self) -> None:
        source = (
            "user-before\n"
            "<!-- memory-bank-codex:start -->\n"
            "managed\n"
            "<!-- memory-bank-codex:end -->\n"
            "user-after\n"
        )
        cleaned, changed = strip_known_blocks(source)
        self.assertTrue(changed)
        self.assertEqual(cleaned, "user-before\nuser-after\n")

    def test_strip_unpaired_marker_refuses_to_change_content(self) -> None:
        source = "user\n<!-- memory-bank:start -->\nmanaged\n"
        cleaned, changed = strip_known_blocks(source)
        self.assertFalse(changed)
        self.assertEqual(cleaned, source)

    def test_clean_hooks_removes_only_memory_bank_commands(self) -> None:
        source = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "echo user"}]},
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "mb-session-turn.sh # [memory-bank-skill]",
                            },
                        ],
                    },
                ],
            },
            "theme": "dark",
        }
        cleaned = clean_hooks_json(source)
        self.assertEqual(cleaned["theme"], "dark")
        self.assertEqual(
            cleaned["hooks"]["Stop"],
            [{"hooks": [{"type": "command", "command": "echo user"}]}],
        )

    def test_manifest_files_must_stay_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / ".codex/.mb-manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(
                    {"files": [str(root / ".codex/hooks.json"), "/tmp/escape"]},
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MbError, "manifest path escapes project"):
                manifest_owned_files(root, manifest)

    def test_remove_opencode_plugin_preserves_unrelated_plugins(self) -> None:
        source = {
            "plugin": ["./.opencode/plugins/memory-bank.js", "user-plugin"],
            "theme": "dark",
        }
        cleaned = remove_opencode_plugin(
            source,
            "./.opencode/plugins/memory-bank.js",
        )
        self.assertEqual(cleaned, {"plugin": ["user-plugin"], "theme": "dark"})


if __name__ == "__main__":
    unittest.main()
