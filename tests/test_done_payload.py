import json
import tempfile
import unittest
from pathlib import Path

from scripts.mb_lite.errors import MbError


class DonePayloadTests(unittest.TestCase):
    def test_revision_changes_when_any_core_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            for name in ("status.md", "checklist.md", "progress.md"):
                (bank / name).write_text(name, encoding="utf-8")
            from scripts.mb_lite.done import bank_revision

            first = bank_revision(bank)
            (bank / "checklist.md").write_text("changed", encoding="utf-8")
            self.assertNotEqual(bank_revision(bank), first)

    def test_payload_requires_fixed_field_types(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": ["Use one central registry"],
                "next_steps": ["Add start renderer"],
                "blockers": [],
                "key_files": ["scripts/mb_lite/registry.py"],
                "completed_items": ["Define registry schema"],
                "new_items": ["Implement recall"],
                "note": None,
            }
        )

        payload = DonePayload.from_json(raw)
        self.assertEqual(payload.summary, "Implemented registry")

    def test_payload_rejects_wrong_list_item_type(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": [7],
                "next_steps": [],
                "blockers": [],
                "key_files": [],
                "completed_items": [],
                "new_items": [],
                "note": None,
            }
        )

        with self.assertRaisesRegex(
            MbError,
            "decisions must contain non-empty strings",
        ):
            DonePayload.from_json(raw)


if __name__ == "__main__":
    unittest.main()
