import json
import tempfile
import unittest
from pathlib import Path

from scripts.mb_lite.errors import MbError


class DonePayloadTests(unittest.TestCase):
    def test_revision_changes_when_any_core_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            core_files = ("status.md", "checklist.md", "progress.md")
            for name in core_files:
                (bank / name).write_text(name, encoding="utf-8")
            from scripts.mb_lite.done import bank_revision

            for name in core_files:
                with self.subTest(core_file=name):
                    original = bank_revision(bank)
                    current = (bank / name).read_text(encoding="utf-8")
                    (bank / name).write_text(f"{current}\nchanged", encoding="utf-8")
                    self.assertNotEqual(bank_revision(bank), original)
                    (bank / name).write_text(current, encoding="utf-8")

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

    def test_payload_accepts_valid_note_object(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": ["Use one central registry"],
                "next_steps": [],
                "blockers": [],
                "key_files": [],
                "completed_items": [],
                "new_items": [],
                "note": {
                    "title": "Session note",
                    "body": "Kept the payload contract small.",
                },
            }
        )

        payload = DonePayload.from_json(raw)
        self.assertIsNotNone(payload.note)
        self.assertEqual(payload.note.title, "Session note")
        self.assertEqual(payload.note.body, "Kept the payload contract small.")

    def test_payload_rejects_blank_note_title(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": ["Use one central registry"],
                "next_steps": [],
                "blockers": [],
                "key_files": [],
                "completed_items": [],
                "new_items": [],
                "note": {"title": "   ", "body": "Body"},
            }
        )

        with self.assertRaisesRegex(
            MbError,
            "note title and body must be non-empty",
        ):
            DonePayload.from_json(raw)

    def test_payload_rejects_blank_note_body(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": ["Use one central registry"],
                "next_steps": [],
                "blockers": [],
                "key_files": [],
                "completed_items": [],
                "new_items": [],
                "note": {"title": "Title", "body": "   "},
            }
        )

        with self.assertRaisesRegex(
            MbError,
            "note title and body must be non-empty",
        ):
            DonePayload.from_json(raw)

    def test_payload_rejects_non_object_note(self) -> None:
        from scripts.mb_lite.done import DonePayload

        raw = json.dumps(
            {
                "bank_revision": "a" * 64,
                "summary": "Implemented registry",
                "decisions": ["Use one central registry"],
                "next_steps": [],
                "blockers": [],
                "key_files": [],
                "completed_items": [],
                "new_items": [],
                "note": "not-an-object",
            }
        )

        with self.assertRaisesRegex(
            MbError,
            "note must be null or an object with title and body",
        ):
            DonePayload.from_json(raw)


if __name__ == "__main__":
    unittest.main()
