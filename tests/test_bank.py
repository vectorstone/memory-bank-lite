import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.mb_lite.bank as bank_module
from scripts.mb_lite.bank import (
    initialize_bank,
    initialize_project_bank,
    template_root,
    validate_bank,
)
from scripts.mb_lite.errors import MbError


class BankTests(unittest.TestCase):
    def test_initialize_creates_files_and_preserves_existing_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            self.assertIsNone(initialize_bank(bank))
            self.assertTrue((bank / "notes").is_dir())
            self.assertTrue((bank / "plans" / "done").is_dir())
            for name in ("status.md", "checklist.md", "progress.md", "roadmap.md", "research.md", "backlog.md", "lessons.md"):
                self.assertTrue((bank / name).is_file())

            status = bank / "status.md"
            status.write_bytes(b"# User Status\n")
            initialize_bank(bank)
            self.assertEqual(status.read_bytes(), b"# User Status\n")

    def test_template_root_points_to_repository_templates(self):
        self.assertEqual(
            template_root(),
            Path(bank_module.__file__).resolve().parents[2] / "templates" / ".memory-bank",
        )

    def test_validate_reports_missing_readable_core_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            bank.mkdir()
            (bank / "status.md").write_text("status\n", encoding="utf-8")
            (bank / "checklist.md").write_text("checklist\n", encoding="utf-8")
            with self.assertRaisesRegex(MbError, r"missing readable progress\.md"):
                validate_bank(bank)

    def test_initialize_rejects_symlinked_status_without_writing_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bank = root / ".memory-bank"
            bank.mkdir()
            outside = root / "outside.md"
            bank.joinpath("status.md").symlink_to(outside)
            with self.assertRaisesRegex(MbError, r"missing readable status\.md") as ctx:
                initialize_bank(bank)
            self.assertEqual(ctx.exception.exit_code, 4)
            self.assertFalse(outside.exists())

    def test_initialize_rejects_symlinked_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            bank.mkdir()
            source = bank / "source.md"
            source.write_text("source\n", encoding="utf-8")
            bank.joinpath("status.md").symlink_to(source)
            with self.assertRaises(MbError):
                initialize_bank(bank)

    def test_initialize_rejects_symlinked_notes_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bank = root / ".memory-bank"
            bank.mkdir()
            bank.joinpath("notes").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(MbError):
                initialize_bank(bank)

    def test_project_initialize_failure_cleans_stage_without_final_bank(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "projects"
            with patch.object(
                bank_module,
                "_populate_bank",
                side_effect=OSError("simulated template failure"),
            ):
                with self.assertRaisesRegex(MbError, "unable to initialize Bank"):
                    initialize_project_bank(projects_root, "repo-123")

            self.assertFalse((projects_root / "repo-123").exists())
            self.assertEqual(list(projects_root.iterdir()), [])

    def test_project_reinitialize_preserves_existing_core_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "projects"
            bank = initialize_project_bank(projects_root, "repo-123")
            status = bank / "status.md"
            status.write_text("# User Status\n", encoding="utf-8")

            initialize_project_bank(projects_root, "repo-123")

            self.assertEqual(status.read_text(encoding="utf-8"), "# User Status\n")

    def test_validate_rejects_invalid_utf8_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            bank.mkdir()
            for name in ("status.md", "checklist.md"):
                (bank / name).write_text("ok\n", encoding="utf-8")
            (bank / "progress.md").write_bytes(b"\xff")
            with self.assertRaisesRegex(MbError, r"missing readable progress\.md"):
                validate_bank(bank)

    def test_validate_rejects_symlinked_core_files(self):
        for linked_name in ("status.md", "checklist.md", "progress.md"):
            with self.subTest(linked_name=linked_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                bank = root / ".memory-bank"
                bank.mkdir()
                outside = root / "outside.md"
                outside.write_text("outside secret\n", encoding="utf-8")
                for name in ("status.md", "checklist.md", "progress.md"):
                    target = bank / name
                    if name == linked_name:
                        target.symlink_to(outside)
                    else:
                        target.write_text(f"{name}\n", encoding="utf-8")

                with self.assertRaisesRegex(MbError, rf"missing readable {linked_name}") as raised:
                    validate_bank(bank)

                self.assertEqual(raised.exception.exit_code, 4)

    def test_validate_rejects_regular_file_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / "bank-file"
            bank.write_text("not a bank\n", encoding="utf-8")
            with self.assertRaisesRegex(MbError, r"Bank directory does not exist:"):
                validate_bank(bank)


if __name__ == "__main__":
    unittest.main()
