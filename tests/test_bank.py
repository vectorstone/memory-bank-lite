import tempfile
import unittest
from pathlib import Path

from scripts.mb_lite.bank import initialize_bank, validate_bank
from scripts.mb_lite.errors import MbError


class BankTests(unittest.TestCase):
    def test_initialize_creates_files_and_preserves_existing_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            initialize_bank(bank)
            self.assertTrue((bank / "notes").is_dir())
            self.assertTrue((bank / "plans" / "done").is_dir())
            for name in ("status.md", "checklist.md", "progress.md", "roadmap.md", "research.md", "backlog.md", "lessons.md"):
                self.assertTrue((bank / name).is_file())

            status = bank / "status.md"
            status.write_bytes(b"# User Status\n")
            initialize_bank(bank)
            self.assertEqual(status.read_bytes(), b"# User Status\n")

    def test_validate_reports_missing_readable_core_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bank = Path(temp_dir) / ".memory-bank"
            bank.mkdir()
            (bank / "status.md").write_text("status\n", encoding="utf-8")
            (bank / "checklist.md").write_text("checklist\n", encoding="utf-8")
            with self.assertRaisesRegex(MbError, r"missing readable progress\.md"):
                validate_bank(bank)


if __name__ == "__main__":
    unittest.main()
