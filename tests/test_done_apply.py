from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.mb_lite.done import DonePayload, apply_done, bank_revision
from scripts.mb_lite.errors import MbError


def payload(bank: Path, note: dict[str, str] | None = None) -> DonePayload:
    return DonePayload.from_json(
        json.dumps(
            {
                "bank_revision": bank_revision(bank),
                "summary": "Implemented registry",
                "decisions": ["Use central storage"],
                "next_steps": ["Implement start"],
                "blockers": [],
                "key_files": ["scripts/mb_lite/registry.py"],
                "completed_items": ["Define registry schema"],
                "new_items": ["Implement start"],
                "note": note,
            }
        )
    )


def _apply_worker(bank: str, repo: str, raw: str, queue) -> None:
    try:
        value = DonePayload.from_json(raw)
        apply_done(
            Path(bank),
            Path(repo),
            value,
            datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        )
        queue.put(0)
    except MbError as exc:
        queue.put(exc.exit_code)


class DoneApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.bank = self.root / "bank"
        self.repo.mkdir()
        self.bank.mkdir()
        (self.repo / "scripts" / "mb_lite").mkdir(parents=True)
        (self.repo / "scripts" / "mb_lite" / "registry.py").write_text(
            "registry = True\n",
            encoding="utf-8",
        )
        (self.bank / "status.md").write_text("# Status\nlegacy\n", encoding="utf-8")
        (self.bank / "checklist.md").write_text(
            "# Checklist\n- [ ] Define registry schema\n",
            encoding="utf-8",
        )
        (self.bank / "progress.md").write_text(
            "# Progress\nlegacy\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_apply_preserves_progress_prefix_and_creates_note_only_when_requested(self) -> None:
        original = (self.bank / "progress.md").read_bytes()
        result = apply_done(
            self.bank,
            self.repo,
            payload(
                self.bank,
                {
                    "title": "Central registry",
                    "body": "Use one registry for all hosts.",
                },
            ),
            datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        )
        self.assertTrue((self.bank / "progress.md").read_bytes().startswith(original))
        self.assertEqual(result.note_path, "notes/2026-07-10_central-registry.md")
        self.assertTrue((self.bank / result.note_path).is_file())
        self.assertTrue((self.bank / ".mb-lite.lock").is_file())

    def test_revision_conflict_writes_nothing(self) -> None:
        draft = payload(self.bank)
        (self.bank / "status.md").write_text(
            "# Status\nchanged elsewhere\n",
            encoding="utf-8",
        )
        before = {
            name: (self.bank / name).read_bytes()
            for name in ("status.md", "checklist.md", "progress.md")
        }
        with self.assertRaisesRegex(MbError, "Bank revision changed") as raised:
            apply_done(self.bank, self.repo, draft, datetime.now(timezone.utc))
        self.assertEqual(raised.exception.exit_code, 3)
        after = {
            name: (self.bank / name).read_bytes()
            for name in ("status.md", "checklist.md", "progress.md")
        }
        self.assertEqual(before, after)

    def test_key_file_outside_repository_is_rejected(self) -> None:
        raw = payload(self.bank)
        invalid = DonePayload(
            raw.bank_revision,
            raw.summary,
            raw.decisions,
            raw.next_steps,
            raw.blockers,
            ("../secret",),
            raw.completed_items,
            raw.new_items,
            raw.note,
        )
        with self.assertRaisesRegex(MbError, "key file is outside repository"):
            apply_done(self.bank, self.repo, invalid, datetime.now(timezone.utc))

    def test_two_concurrent_applies_allow_one_writer(self) -> None:
        draft = payload(self.bank)
        raw = json.dumps(
            {
                "bank_revision": draft.bank_revision,
                "summary": draft.summary,
                "decisions": list(draft.decisions),
                "next_steps": list(draft.next_steps),
                "blockers": list(draft.blockers),
                "key_files": list(draft.key_files),
                "completed_items": list(draft.completed_items),
                "new_items": list(draft.new_items),
                "note": None,
            }
        )
        context = get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_apply_worker,
                args=(str(self.bank), str(self.repo), raw, queue),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(
            sorted(queue.get(timeout=2) for _ in range(2)),
            [0, 3],
        )

    def test_apply_without_note_does_not_create_note_directory(self) -> None:
        result = apply_done(
            self.bank,
            self.repo,
            payload(self.bank),
            datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(result.note_path)
        self.assertFalse((self.bank / "notes").exists())

    def test_payload_text_is_data_and_is_never_executed(self) -> None:
        sentinel = self.root / "executed"
        raw = payload(self.bank)
        hostile = DonePayload(
            raw.bank_revision,
            f"$(touch {sentinel})",
            raw.decisions,
            raw.next_steps,
            raw.blockers,
            raw.key_files,
            raw.completed_items,
            raw.new_items,
            raw.note,
        )
        apply_done(self.bank, self.repo, hostile, datetime.now(timezone.utc))
        self.assertFalse(sentinel.exists())

    def test_failed_later_replace_rolls_back_earlier_files(self) -> None:
        before = {
            name: (self.bank / name).read_bytes()
            for name in ("status.md", "checklist.md", "progress.md")
        }
        from scripts.mb_lite import done as done_module

        real_commit = done_module._commit_staged_file
        calls = {"count": 0}

        def flaky_commit(path: Path, staged_name: str) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated replace failure")
            real_commit(path, staged_name)

        with patch.object(done_module, "_commit_staged_file", side_effect=flaky_commit):
            with self.assertRaisesRegex(MbError, "unable to write bank"):
                apply_done(self.bank, self.repo, payload(self.bank), datetime.now(timezone.utc))
        after = {
            name: (self.bank / name).read_bytes()
            for name in ("status.md", "checklist.md", "progress.md")
        }
        self.assertEqual(before, after)
        self.assertFalse((self.bank / "notes").exists())


if __name__ == "__main__":
    unittest.main()
