import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.recall import RecallResult, expand_result, search


class RecallTests(unittest.TestCase):
    def test_search_and_expand_are_read_only_and_return_stable_note(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / ".memory-bank"
            (bank / "notes").mkdir(parents=True)
            note = bank / "notes" / "registry.md"
            note.write_text("# Registry\n\nThe central registry decision.\n", encoding="utf-8")
            before = note.read_bytes()

            results = search(bank, "central registry")
            self.assertEqual(len(results), 1)
            result = results[0]
            expected = hashlib.sha256(b"notes/registry.md\0Registry\0" + b"0").hexdigest()[:12]
            self.assertEqual(result, RecallResult(expected, "notes/registry.md", "Registry", 0, "# Registry\nThe central registry decision."))
            expanded = expand_result(bank, result.result_id)
            self.assertEqual(expanded.path, "notes/registry.md")
            self.assertEqual(expanded.heading, "Registry")
            self.assertEqual(expanded.offset, 0)
            self.assertEqual(expanded.snippet, "# Registry\n\nThe central registry decision.")
            self.assertEqual(note.read_bytes(), before)

    def test_missing_optional_directories_and_empty_query(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / ".memory-bank"
            bank.mkdir()
            self.assertEqual(search(bank, "absent"), [])
            with self.assertRaises(MbError) as raised:
                search(bank, "a")
            self.assertEqual(raised.exception.exit_code, 1)

    def test_caps_results_and_unknown_id_fails(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / ".memory-bank"
            (bank / "notes").mkdir(parents=True)
            for index in range(12):
                (bank / "notes" / f"{index:02d}.md").write_text("# Match\nmatch\n", encoding="utf-8")
            results = search(bank, "match")
            self.assertEqual(len(results), 10)
            self.assertEqual([item.path for item in results], [f"notes/{i:02d}.md" for i in range(10)])
            with self.assertRaises(MbError) as raised:
                expand_result(bank, "unknown")
            self.assertEqual(raised.exception.exit_code, 1)

    def test_searches_plan_sources_and_is_stable(self):
        with tempfile.TemporaryDirectory() as root:
            bank = Path(root) / ".memory-bank"
            (bank / "plans" / "done").mkdir(parents=True)
            (bank / "plans" / "active.md").write_text("# Plan\nregistry active\n", encoding="utf-8")
            (bank / "plans" / "done" / "done.md").write_text("# Done\nregistry done\n", encoding="utf-8")
            first = search(bank, "registry")
            self.assertEqual(first, search(bank, "registry"))
            self.assertEqual([item.path for item in first], ["plans/active.md", "plans/done/done.md"])

    def test_symlink_sources_are_not_read(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            bank = Path(root) / ".memory-bank"
            (bank / "notes").mkdir(parents=True)
            outside_note = Path(outside) / "secret.md"
            outside_note.write_text("# Secret\nregistry outside\n", encoding="utf-8")
            (bank / "notes" / "linked.md").symlink_to(outside_note)
            (bank / "plans").mkdir()
            (bank / "plans" / "linked.md").symlink_to(outside_note)
            (bank / "progress.md").symlink_to(outside_note)
            self.assertEqual(search(bank, "registry"), [])

    def test_symlinked_optional_directories_are_not_read(self):
        for directory_name in ("notes", "plans"):
            with self.subTest(directory_name=directory_name), tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
                bank = Path(root) / ".memory-bank"
                bank.mkdir()
                outside_dir = Path(outside) / directory_name
                outside_dir.mkdir()
                (outside_dir / "secret.md").write_text(
                    "# Secret\nregistry outside\n",
                    encoding="utf-8",
                )
                (bank / directory_name).symlink_to(outside_dir, target_is_directory=True)

                self.assertEqual(search(bank, "registry"), [])


if __name__ == "__main__":
    unittest.main()
