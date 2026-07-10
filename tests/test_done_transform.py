import unittest

from scripts.mb_lite.done import DonePayload
from scripts.mb_lite.errors import MbError


PAYLOAD = DonePayload.from_json(
    """{
  "bank_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "summary": "Implemented registry",
  "decisions": ["Use one central registry"],
  "next_steps": ["Implement recall"],
  "blockers": [],
  "key_files": ["scripts/mb_lite/registry.py"],
  "completed_items": ["Define registry schema"],
  "new_items": ["Implement recall"],
  "note": null
}"""
)


class DoneTransformTests(unittest.TestCase):
    def test_status_replaces_only_lite_block(self) -> None:
        from scripts.mb_lite.done import render_status

        source = (
            "# Status\n"
            "user-before\n"
            "<!-- mb-lite:current:start -->\n"
            "old\n"
            "<!-- mb-lite:current:end -->\n"
            "user-after\n"
        )

        result = render_status(source, PAYLOAD)

        self.assertTrue(result.startswith("# Status\nuser-before\n"))
        self.assertTrue(result.endswith("user-after\n"))
        self.assertIn("**Focus:** Implemented registry", result)
        self.assertIn("**Next:** Implement recall", result)

    def test_checklist_completes_exact_item_and_adds_new_item(self) -> None:
        from scripts.mb_lite.done import render_checklist

        source = "# Checklist\n- [ ] Define registry schema\n- [ ] Keep exact\n"

        result = render_checklist(source, PAYLOAD)

        self.assertIn("- [x] Define registry schema", result)
        self.assertIn("- [ ] Keep exact", result)
        self.assertIn("- [ ] Implement recall", result)

    def test_checklist_ambiguous_completion_fails(self) -> None:
        from scripts.mb_lite.done import render_checklist

        source = "- [ ] Define registry schema\n- [ ] Define registry schema\n"

        with self.assertRaisesRegex(MbError, "expected exactly one unchecked item"):
            render_checklist(source, PAYLOAD)

    def test_progress_entry_has_stable_marker_and_fixed_sections(self) -> None:
        from scripts.mb_lite.done import render_progress_entry

        result = render_progress_entry(PAYLOAD, "2026-07-10T12:00:00Z")

        self.assertRegex(
            result,
            r"<!-- mb-lite-entry:[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8} -->",
        )
        self.assertIn("## Decisions\n- Use one central registry", result)
        self.assertIn("## Key Files\n- `scripts/mb_lite/registry.py`", result)


if __name__ == "__main__":
    unittest.main()
