from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.mb_lite.errors import MbError
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

    def test_rollback_replaces_destination_symlink_without_touching_victim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            config = root / "config.json"
            victim = root / "victim.json"
            config.write_bytes(b"original\n")
            victim.write_bytes(b"victim\n")
            transaction = BackupTransaction.create(state, "symlink-swap")
            transaction.capture(config, "restore config")
            transaction.finish()
            config.unlink()
            config.symlink_to(victim)

            with patch(
                "scripts.mb_lite.transaction.os.fsync",
                wraps=os.fsync,
            ) as fsync:
                BackupTransaction.rollback(transaction.directory)

            self.assertFalse(config.is_symlink())
            self.assertEqual(config.read_bytes(), b"original\n")
            self.assertEqual(victim.read_bytes(), b"victim\n")
            self.assertGreaterEqual(fsync.call_count, 2)

    def test_create_fsyncs_state_root_after_transaction_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"

            with patch("scripts.mb_lite.transaction.os.fsync") as fsync:
                transaction = BackupTransaction.create(state, "durable-create")

            self.assertTrue(transaction.directory.is_dir())
            fsync.assert_called_once()

    def test_rollback_rejects_entire_malformed_manifest_before_restoring(self) -> None:
        malformed_records = (
            {"version": 2, "records": []},
            {"version": 1},
            {
                "version": 1,
                "records": [
                    {
                        "path": "/tmp/unknown",
                        "backup": None,
                        "sha256": None,
                        "action": "unknown",
                        "kind": "unknown",
                    },
                ],
            },
            "traversal",
            "nul",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, malformed in enumerate(malformed_records):
                with self.subTest(case=malformed):
                    transaction = root / f"transaction-{index}"
                    transaction.mkdir()
                    destination = root / f"config-{index}.json"
                    destination.write_bytes(b"current\n")
                    valid_backup = transaction / "0000-config.json"
                    valid_backup.write_bytes(b"restored\n")
                    valid_record = {
                        "path": str(destination),
                        "backup": valid_backup.name,
                        "sha256": sha256(b"restored\n").hexdigest(),
                        "action": "restore config",
                        "kind": "file",
                    }
                    if malformed == "traversal":
                        payload = {
                            "version": 1,
                            "records": [
                                {
                                    **valid_record,
                                    "backup": "../outside",
                                },
                                valid_record,
                            ],
                        }
                    elif malformed == "nul":
                        payload = {
                            "version": 1,
                            "records": [
                                {**valid_record, "backup": "bad\0name"},
                                valid_record,
                            ],
                        }
                    elif isinstance(malformed, dict) and malformed.get("records"):
                        payload = {
                            **malformed,
                            "records": [*malformed["records"], valid_record],
                        }
                    else:
                        payload = malformed
                    (transaction / "manifest.json").write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(
                        MbError,
                        "invalid rollback manifest",
                    ) as raised:
                        BackupTransaction.rollback(transaction)

                    self.assertEqual(raised.exception.exit_code, 4)
                    self.assertEqual(destination.read_bytes(), b"current\n")


if __name__ == "__main__":
    unittest.main()
