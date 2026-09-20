from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.archive import (
    PROGRESS_MAX_ENTRIES,
    merge_archive_text,
    plan_progress_roll,
    split_progress,
)
from scripts.mb_lite.attention import parse_attention
from scripts.mb_lite.bank import initialize_bank
from scripts.mb_lite.compact import apply_compact, compact_preview
from scripts.mb_lite.done import DonePayload, apply_done, render_progress_entry, v2_bank_revision
from scripts.mb_lite.distill import DistillPayload, apply_distill, distill_snapshot
from scripts.mb_lite.errors import MbError
from scripts.mb_lite.recall import search

ATTENTION_TEMPLATE = (
    "# Attention\n\n"
    "<!-- mb-lite:attention:start -->\n## Active Attention\n\n{body}\n<!-- mb-lite:attention:end -->\n"
)


def attention_entry(entry_id: str, status: str = "active") -> str:
    return (
        f"<!-- mb-lite-attention:{entry_id} -->\n"
        f"### {entry_id} [medium] item {entry_id}\n\n"
        f"- **Status:** {status}\n"
        f"- **Scope:** test\n"
        f"- **Reason:** reason {entry_id}\n"
        f"- **Evidence:** evidence {entry_id}\n"
        f"- **Verified:** 2026-09-01\n"
        f"- **Review after:** 90 days\n"
    )


def _entry(marker_date: str, summary: str = "work") -> str:
    return (
        f"<!-- mb-lite-entry:{marker_date}-abcdef01 -->\n\n"
        f"### 2026-08-{marker_date[6:8]} 10:00:00 - {summary}\n\n"
        f"## Decisions\n- {summary}\n"
    )


class PlanRollTest(unittest.TestCase):
    def test_rolls_oldest_beyond_entry_limit(self):
        source = "# Progress\n" + "\n".join(_entry(f"20260801T1000{i:02d}Z") for i in range(PROGRESS_MAX_ENTRIES + 2))
        roll = plan_progress_roll(source)
        self.assertEqual(roll.archived_count, 2)
        _, retained_entries = split_progress(roll.retained)
        self.assertEqual(len(retained_entries), PROGRESS_MAX_ENTRIES)
        self.assertIn("20260801T100000Z", roll.archived[0].raw)
        self.assertNotIn("abcdef01", roll.retained.split("mb-lite-entry:", 1)[0])

    def test_byte_limit_rolls_even_under_entry_limit(self):
        big = _entry("20260801T100000Z", "x" * 5000) + _entry("20260802T100000Z", "y" * 5000) + _entry("20260803T100000Z", "z")
        roll = plan_progress_roll("# Progress\n" + big, max_entries=15, max_bytes=12000)
        self.assertGreaterEqual(roll.archived_count, 1)
        self.assertLessEqual(len(roll.retained.encode("utf-8")), 12000 + 6000)

    def test_never_rolls_final_entry(self):
        single = "# Progress\n" + _entry("20260801T100000Z", "x" * 90000)
        roll = plan_progress_roll(single)
        self.assertEqual(roll.archived_count, 0)

    def test_merge_archive_appends_with_header(self):
        fresh = merge_archive_text(None, (_entry("20260801T100000Z"),), "# Progress Archive 2026")
        self.assertTrue(fresh.startswith("# Progress Archive 2026\n\n<!-- mb-lite-entry:"))
        grown = merge_archive_text(fresh, (_entry("20260802T100000Z"),), "# Progress Archive 2026")
        self.assertEqual(grown.count("mb-lite-entry:"), 2)


class DoneRollingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.bank = self.root / "bank"
        self.repo.mkdir()
        initialize_bank(self.bank)
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _apply(self, index: int, note_title: str | None = None) -> object:
        raw = {
            "bank_revision": v2_bank_revision(self.bank),
            "summary": f"step {index}",
            "decisions": [f"decision {index}"],
            "next_steps": [],
            "blockers": [],
            "key_files": [],
            "completed_items": [],
            "new_items": [],
            "note": {"title": note_title, "body": "body"} if note_title else None,
        }
        return apply_done(
            self.bank,
            self.repo,
            DonePayload.from_json(json.dumps(raw)),
            datetime(2026, 8, 1 + index % 27, 12, 0, tzinfo=timezone.utc),
            revision_function=v2_bank_revision,
        )

    def test_done_rolls_progress_into_archive(self):
        for index in range(PROGRESS_MAX_ENTRIES + 1):
            self._apply(index)
        progress = (self.bank / "progress.md").read_text(encoding="utf-8")
        _, entries = split_progress(progress)
        self.assertEqual(len(entries), PROGRESS_MAX_ENTRIES)
        archive = (self.bank / "archive" / "progress-2026.md").read_text(encoding="utf-8")
        self.assertIn("step 0", archive)
        self.assertNotIn("step 0\n", progress)
        self.assertIn("step 15", progress)
        results = search(self.bank, "decision 0")
        self.assertTrue(any(item.path.startswith("archive/") for item in results))

    def test_note_collision_takes_suffix_and_links_from_progress(self):
        first = self._apply(0, note_title="Same Title")
        second = self._apply(1, note_title="Same Title")
        self.assertEqual(first.note_path, "notes/2026-08-01_same-title.md")
        self.assertEqual(second.note_path, "notes/2026-08-02_same-title.md")
        result = apply_done(
            self.bank,
            self.repo,
            DonePayload.from_json(json.dumps({
                "bank_revision": v2_bank_revision(self.bank),
                "summary": "same day",
                "decisions": [], "next_steps": [], "blockers": [],
                "key_files": [], "completed_items": [], "new_items": [],
                "note": {"title": "Same Title", "body": "again"},
            })),
            datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc),
            revision_function=v2_bank_revision,
        )
        self.assertEqual(result.note_path, "notes/2026-08-01_same-title-2.md")
        progress = (self.bank / "progress.md").read_text(encoding="utf-8")
        self.assertIn("- Note: `notes/2026-08-01_same-title-2.md`", progress)

    def test_attention_resolve_moves_to_archive(self):
        (self.bank / "attention.md").write_text(
            ATTENTION_TEMPLATE.format(body=attention_entry("ATT-001") + attention_entry("ATT-002")),
            encoding="utf-8",
        )
        raw = {
            "bank_revision": v2_bank_revision(self.bank),
            "summary": "resolve one",
            "decisions": [], "next_steps": [], "blockers": [],
            "key_files": [], "completed_items": [], "new_items": [],
            "attention_changes": [{"action": "resolve", "attention_id": "ATT-001"}],
        }
        result = apply_done(
            self.bank, self.repo, DonePayload.from_json(json.dumps(raw)),
            datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            revision_function=v2_bank_revision,
        )
        self.assertEqual(result.archived_attention, 1)
        active = parse_attention((self.bank / "attention.md").read_text(encoding="utf-8"))
        self.assertEqual([entry.attention_id for entry in active], ["ATT-002"])
        archive = (self.bank / "archive" / "attention.md").read_text(encoding="utf-8")
        self.assertIn("ATT-001", archive)
        self.assertIn("**Status:** resolved", archive)

    def test_attention_update_can_reactivate_archived_item(self):
        (self.bank / "attention.md").write_text(
            ATTENTION_TEMPLATE.format(body=attention_entry("ATT-001")),
            encoding="utf-8",
        )
        (self.bank / "archive").mkdir()
        (self.bank / "archive" / "attention.md").write_text(
            "# Attention Archive\n\n" + attention_entry("ATT-000", status="resolved"),
            encoding="utf-8",
        )
        raw = {
            "bank_revision": v2_bank_revision(self.bank),
            "summary": "reopen",
            "decisions": [], "next_steps": [], "blockers": [],
            "key_files": [], "completed_items": [], "new_items": [],
            "attention_changes": [{"action": "update", "attention_id": "ATT-000", "severity": "high", "status": "active"}],
        }
        apply_done(
            self.bank, self.repo, DonePayload.from_json(json.dumps(raw)),
            datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            revision_function=v2_bank_revision,
        )
        active = parse_attention((self.bank / "attention.md").read_text(encoding="utf-8"))
        self.assertEqual([entry.attention_id for entry in active], ["ATT-001", "ATT-000"])
        self.assertEqual(active[1].severity, "high")


class CompactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.bank = Path(self.temp.name) / "bank"
        initialize_bank(self.bank)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compact_nothing_pending_fails_apply(self):
        preview = compact_preview(self.bank)
        self.assertFalse(preview["changes_pending"])
        with self.assertRaisesRegex(MbError, "nothing to compact"):
            apply_compact(self.bank)

    def test_compact_moves_closed_attention_and_rolls_progress(self):
        (self.bank / "attention.md").write_text(
            ATTENTION_TEMPLATE.format(body=attention_entry("ATT-001") + attention_entry("ATT-002", status="resolved")),
            encoding="utf-8",
        )
        (self.bank / "progress.md").write_text(
            "# Progress\n" + "\n".join(_entry(f"2026080{i}T10000{i}Z") for i in range(1, 10)) + "\n",
            encoding="utf-8",
        )
        preview = compact_preview(self.bank)
        self.assertEqual(preview["attention"]["would_archive"], 1)
        self.assertEqual(preview["progress"]["would_archive"], 0)
        (self.bank / "progress.md").write_text(
            "# Progress\n"
            + "\n".join(_entry(f"202608{i:02d}T100000Z", f"item {i}") for i in range(1, 20))
            + "\n",
            encoding="utf-8",
        )
        preview = compact_preview(self.bank)
        self.assertEqual(preview["progress"]["would_archive"], 19 - PROGRESS_MAX_ENTRIES)
        result = apply_compact(self.bank)
        self.assertEqual(result["archived_attention"], 1)
        self.assertEqual(result["archived_entries"], 19 - PROGRESS_MAX_ENTRIES)
        active = parse_attention((self.bank / "attention.md").read_text(encoding="utf-8"))
        self.assertEqual([entry.attention_id for entry in active], ["ATT-001"])
        after = compact_preview(self.bank)
        self.assertFalse(after["changes_pending"])
        _, entries = split_progress((self.bank / "progress.md").read_text(encoding="utf-8"))
        self.assertEqual(len(entries), PROGRESS_MAX_ENTRIES)


class DistillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.bank = Path(self.temp.name) / "bank"
        initialize_bank(self.bank)
        entry = render_progress_entry(
            DonePayload.from_json(json.dumps({
                "bank_revision": "0" * 64, "summary": "docker digest pinning",
                "decisions": ["pin digests"], "next_steps": [], "blockers": [],
                "key_files": [], "completed_items": [], "new_items": [],
            })),
            "2026-08-13T15:11:34Z",
        )
        (self.bank / "progress.md").write_text("# Progress\n\n" + entry, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _payload(self, lessons: list[dict], revision: str | None = None) -> str:
        return json.dumps({
            "bank_revision": revision or distill_snapshot(self.bank)["bank_revision"],
            "lessons": lessons,
        })

    def test_distill_creates_lessons_and_recall_finds_them(self):
        snapshot = distill_snapshot(self.bank)
        marker = snapshot["entries"][0]["marker"]
        self.assertFalse(snapshot["lessons_file_present"])
        result = apply_distill(self.bank, DistillPayload.from_json(self._payload([
            {"title": "Pin production images by digest", "body": "Always pin Docker Hub manifests by digest.", "sources": [marker]},
        ])))
        self.assertEqual(result["lessons_added"], 1)
        lessons = (self.bank / "lessons.md").read_text(encoding="utf-8")
        self.assertIn("### LSN-001 Pin production images by digest", lessons)
        self.assertIn(marker, lessons)
        results = search(self.bank, "digest")
        self.assertEqual(results[0].path, "lessons.md")

    def test_distill_rejects_unknown_marker_and_duplicate_title(self):
        snapshot = distill_snapshot(self.bank)
        marker = snapshot["entries"][0]["marker"]
        apply_distill(self.bank, DistillPayload.from_json(self._payload([
            {"title": "First lesson", "body": "b"},
        ])))
        with self.assertRaisesRegex(MbError, "unknown source entry marker"):
            apply_distill(self.bank, DistillPayload.from_json(self._payload([
                {"title": "Second", "body": "b", "sources": ["<!-- mb-lite-entry:20990101T000000Z-00000000 -->"]},
            ])))
        with self.assertRaisesRegex(MbError, "lesson already exists"):
            apply_distill(self.bank, DistillPayload.from_json(self._payload([
                {"title": "First lesson", "body": "b"},
            ])))

    def test_distill_appends_second_lesson(self):
        apply_distill(self.bank, DistillPayload.from_json(self._payload([
            {"title": "First", "body": "b"},
        ])))
        result = apply_distill(self.bank, DistillPayload.from_json(self._payload([
            {"title": "Second", "body": "b"},
        ])))
        self.assertEqual(result["lesson_ids"], ["LSN-002"])
        lessons = (self.bank / "lessons.md").read_text(encoding="utf-8")
        self.assertEqual(lessons.count("<!-- mb-lite:lessons:start -->"), 1)
        self.assertIn("### LSN-002 Second", lessons)

    def test_distill_revision_conflict(self):
        stale = self._payload([{"title": "Stale", "body": "b"}])
        (self.bank / "status.md").write_text("# Status\nchanged\n", encoding="utf-8")
        with self.assertRaisesRegex(MbError, "Bank revision changed"):
            apply_distill(self.bank, DistillPayload.from_json(stale))


if __name__ == "__main__":
    unittest.main()
