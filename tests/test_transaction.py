from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.transaction import BackupTransaction


class TransactionTest(unittest.TestCase):
    def test_rollback_restores_original_bytes_and_removes_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            existing = root / "config.json"
            created = root / "new.json"
            existing.write_bytes(b"original\n")
            transaction = BackupTransaction.create(state, "20260710-120000")
            transaction.capture(existing, "clean hooks")
            transaction.capture_missing(created, "create registry")
            existing.write_bytes(b"changed\n")
            created.write_bytes(b"new\n")
            transaction.finish()
            BackupTransaction.rollback(state / "20260710-120000")
            self.assertEqual(existing.read_bytes(), b"original\n")
            self.assertFalse(created.exists())

    def test_rollback_restores_removed_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            source = root / "source"
            link = root / "skill"
            source.mkdir()
            link.symlink_to(source, target_is_directory=True)
            transaction = BackupTransaction.create(state, "20260710-120001")
            transaction.capture(link, "remove legacy skill link")
            link.unlink()
            transaction.finish()
            BackupTransaction.rollback(state / "20260710-120001")
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), source.resolve())


if __name__ == "__main__":
    unittest.main()
