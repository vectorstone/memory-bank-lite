import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.mb_lite.attention import AttentionEntry, render_attention
from scripts.mb_lite.bank import initialize_bank
from scripts.mb_lite.errors import MbError
from scripts.mb_lite.handoff import compose_handoff_file
from scripts.mb_lite.start import (
    build_start_context,
    build_v2_start_context,
    build_workspace_start_context,
)


class StartTests(unittest.TestCase):
    def make_bank(self, root, status, checklist, progress):
        bank = Path(root) / ".memory-bank"
        (bank / "plans" / "done").mkdir(parents=True)
        (bank / "notes").mkdir()
        (bank / "status.md").write_text(status, encoding="utf-8")
        (bank / "checklist.md").write_text(checklist, encoding="utf-8")
        (bank / "progress.md").write_text(progress, encoding="utf-8")
        return bank

    def test_start_returns_open_items_and_three_recent_entries_without_writes(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** Registry\n**Next:** Ship\n**Blockers:** None\n", "- [ ] one\n- [x] done\n", "\n".join(f"<!-- mb-lite-entry:id-{i} -->\n### Entry {i}\nbody" for i in range(1, 5)))
            before = {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()}
            output = build_start_context(bank)
            self.assertIn("- [ ] one", output)
            self.assertNotIn("done", output)
            self.assertIn("Entry 2", output)
            self.assertIn("Entry 4", output)
            self.assertEqual(before, {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()})

    def test_legacy_fill_without_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** X\n", "- [ ] item\n", "### Legacy one\na\n### Legacy two\nb\n<!-- mb-lite-entry:new -->\n### New\nc\n")
            output = build_start_context(bank, "recent-progress")
            self.assertIn("Legacy one", output)
            self.assertIn("Legacy two", output)
            self.assertEqual(output.count("New"), 1)

    def test_legacy_entry_matching_lite_entry_is_emitted_once(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** X\n", "- [ ] item\n", "### Same entry\nbody\n<!-- mb-lite-entry:new -->\n### Same entry\nbody\n")
            output = build_start_context(bank, "recent-progress")
            self.assertEqual(output.count("### Same entry"), 1)

    def test_first_open_stage_and_at_most_five_matching_note_titles(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** Registry\n", "- [ ] item\n", "")
            (bank / "plans" / "2026.md").write_text("### Stage 1: Done\n- [x] yes\n### Stage 2: Registry parser\n- [ ] parse\ndetail\n", encoding="utf-8")
            (bank / "plans" / "000-old.md").write_text("### Stage 1: Old\n- [ ] old\n", encoding="utf-8")
            (bank / "plans" / "done" / "999.md").write_text("### Stage 9: Ignore\n- [ ] no\n", encoding="utf-8")
            for i in range(7):
                (bank / "notes" / f"n{i}.md").write_text(f"# Registry note {i}\n", encoding="utf-8")
            output = build_start_context(bank)
            self.assertIn("Stage 2: Registry parser", output)
            self.assertNotIn("Stage 1: Done", output)
            self.assertNotIn("Ignore", output)
            self.assertEqual(output.count("Registry note"), 5)

    def test_protected_sections_over_cap_fail_instead_of_truncating(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** " + "x" * 9000, "- [ ] item\n", "")
            with self.assertRaises(MbError) as ctx:
                build_start_context(bank)
            self.assertIn("current=", str(ctx.exception))

    def test_trims_notes_then_progress_then_stage_detail(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "**Focus:** Registry\n", "- [ ] item\n", "<!-- mb-lite-entry:new -->\n### New\n" + "p" * 3000)
            (bank / "plans" / "a.md").write_text("### Stage 1: Registry\n- [ ] x\n" + "s" * 6000, encoding="utf-8")
            (bank / "notes" / "n.md").write_text("# Registry\n" + "n" * 1000, encoding="utf-8")
            output = build_start_context(bank)
            self.assertLessEqual(len(output.encode()), 8192)
            self.assertIn("Older optional context was trimmed", output)

    def test_optional_symlink_sources_never_disclose_outside_content(self):
        scenarios = ("plans-directory", "plans-file", "notes-directory", "notes-file")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as root:
                base = Path(root)
                bank = self.make_bank(
                    root,
                    "**Focus:** Outside Secret\n",
                    "- [ ] item\n",
                    "",
                )
                outside = base / "outside"
                outside.mkdir()
                if scenario.startswith("plans"):
                    secret = outside / "secret.md"
                    secret.write_text(
                        "### Stage 9: Outside Secret\n- [ ] disclose\n",
                        encoding="utf-8",
                    )
                    if scenario == "plans-directory":
                        for child in (bank / "plans" / "done").iterdir():
                            child.unlink()
                        (bank / "plans" / "done").rmdir()
                        (bank / "plans").rmdir()
                        (bank / "plans").symlink_to(outside, target_is_directory=True)
                    else:
                        (bank / "plans" / "secret.md").symlink_to(secret)
                else:
                    secret = outside / "secret.md"
                    secret.write_text("# Outside Secret Note\n", encoding="utf-8")
                    if scenario == "notes-directory":
                        (bank / "notes").rmdir()
                        (bank / "notes").symlink_to(outside, target_is_directory=True)
                    else:
                        (bank / "notes" / "secret.md").symlink_to(secret)

                output = build_start_context(bank)

                self.assertNotIn("Stage 9: Outside Secret", output)
                self.assertNotIn("Outside Secret Note", output)

    def test_unknown_section(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(root, "", "", "")
            with self.assertRaises(MbError):
                build_start_context(bank, "bad")


class V2StartBudgetTests(unittest.TestCase):
    """Regression tests for the 2026-11-08 start-budget incident.

    ``build_v2_start_context`` used to hard-fail with "protected start sections
    exceed 8192 bytes after attention" because the v1 trim loop packed optional
    content (recent progress) up to the full 8192-byte cap without reserving the
    mandatory handoff/attention tail, so the attention block could not fit even
    one line. The tail must instead degrade explicitly with omission notices.
    """

    def make_v2_bank(self, root: Path) -> Path:
        bank = Path(root) / ".memory-bank"
        initialize_bank(bank)
        (bank / "status.md").write_text(
            "**Focus:** " + "f" * 1160 + "\n**Next:** ship it\n**Blockers:** None\n",
            encoding="utf-8",
        )
        items = "\n".join(f"- [ ] open item {i:02d} " + "o" * 96 for i in range(1, 41))
        (bank / "checklist.md").write_text("# Checklist\n\n" + items + "\n", encoding="utf-8")
        entries = []
        for index, total in enumerate((2719, 2716, 2103), start=1):
            marker = f"<!-- mb-lite-entry:2026110{index}T120000Z-0000000{index} -->"
            heading = f"### 2026-11-0{index} 12:00:00 - session summary {index}"
            body = "p" * (total - len(marker) - len(heading) - 2)
            entries.append(f"{marker}\n{heading}\n{body}")
        (bank / "progress.md").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
        source = (bank / "attention.md").read_text(encoding="utf-8")
        attention = tuple(
            AttentionEntry(
                f"ATT-{i:03d}",
                f"topic {i} " + "t" * 140,
                "project",
                "high" if i == 1 else "medium",
                "active",
                "shared reason",
                "evidence",
                date(2026, 9, 14),
                90,
            )
            for i in range(1, 10)
        )
        (bank / "attention.md").write_text(render_attention(source, attention), encoding="utf-8")
        handoff_body = "### 2026-11-08 15:33:03 - deploy the release and verify production " + "h" * 1400
        (bank / "handoff.md").write_text(compose_handoff_file(None, handoff_body), encoding="utf-8")
        return bank

    def test_incident_budget_degrades_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_v2_bank(Path(root))
            output = build_v2_start_context(bank)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("## Attention", output)
            self.assertIn("ATT-001", output)
            self.assertIn("## Handoff", output)
            before = {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()}
            build_v2_start_context(bank)
            self.assertEqual(before, {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()})

    def test_attention_omission_is_counted_not_silent(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_v2_bank(Path(root))
            source = (bank / "attention.md").read_text(encoding="utf-8")
            attention = tuple(
                AttentionEntry(
                    f"ATT-{i:03d}",
                    "short critical topic" if i == 1 else "t" * 400,
                    "project",
                    "high" if i == 1 else "medium",
                    "active",
                    "reason",
                    "evidence",
                    date(2026, 9, 14),
                    90,
                )
                for i in range(1, 10)
            )
            (bank / "attention.md").write_text(render_attention(source, attention), encoding="utf-8")
            output = build_v2_start_context(bank)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("ATT-001", output)
            self.assertRegex(
                output,
                r"- \.\.\. [1-9] attention items omitted; use start --section attention",
            )

    def test_attention_still_fails_closed_when_protected_leaves_no_room(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_v2_bank(Path(root))
            (bank / "status.md").write_text(
                "**Focus:** " + "f" * 7970 + "\n**Next:** ship\n**Blockers:** None\n",
                encoding="utf-8",
            )
            (bank / "checklist.md").write_text("# Checklist\n\n", encoding="utf-8")
            with self.assertRaises(MbError) as ctx:
                build_v2_start_context(bank)
            self.assertIn("exceed 8192 bytes after attention", str(ctx.exception))


class V2StartLessonsTests(unittest.TestCase):
    def make_bank(self, root: Path) -> Path:
        bank = Path(root) / ".memory-bank"
        initialize_bank(bank)
        (bank / "status.md").write_text(
            "**Focus:** lessons visibility\n**Next:** ship\n**Blockers:** None\n",
            encoding="utf-8",
        )
        blocks = [
            f"<!-- mb-lite-lesson:LSN-{index:03d} -->\n"
            f"### LSN-{index:03d} Lesson title {index}\n\nbody {index}"
            for index in range(1, 6)
        ]
        (bank / "lessons.md").write_text(
            "# Lessons\n\n<!-- mb-lite:lessons:start -->\n## Verified Lessons\n\n"
            + "\n\n".join(blocks)
            + "\n<!-- mb-lite:lessons:end -->\n",
            encoding="utf-8",
        )
        return bank

    def test_default_start_shows_a_bounded_lessons_summary(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(Path(root))
            output = build_v2_start_context(bank)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("## Verified Lessons", output)
            self.assertIn("LSN-001 Lesson title 1", output)
            self.assertIn("LSN-003 Lesson title 3", output)
            self.assertNotIn("LSN-004", output)
            self.assertIn("2 more lessons; use start --section lessons", output)

    def test_lessons_section_returns_every_distilled_lesson(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_bank(Path(root))
            output = build_v2_start_context(bank, "lessons")
            self.assertIn("LSN-005 Lesson title 5", output)


class WorkspaceStartBudgetTests(unittest.TestCase):
    """Regression tests for the 2026-11-22 workspace start-budget incident.

    ``build_workspace_start_context`` used to hard-fail with "Workspace start
    output exceeds 8192 bytes" on the sub-store workspace: the v1 trim loop
    packed the workspace Bank against the full cap without reserving the
    Members block, and the degradation loop refused to drop the last member.
    Members must instead get a reserved budget up front (the sibling of the
    2026-11-08 handoff/attention fix) and degrade with a counted omission
    notice — down to zero visible members — before failing closed.
    """

    def make_small_bank(self, root: Path) -> Path:
        bank = Path(root) / ".memory-bank"
        initialize_bank(bank)
        (bank / "status.md").write_text(
            "**Focus:** workspace registry\n**Next:** ship\n**Blockers:** None\n",
            encoding="utf-8",
        )
        (bank / "checklist.md").write_text("# Checklist\n\n- [ ] one item\n", encoding="utf-8")
        (bank / "progress.md").write_text("", encoding="utf-8")
        return bank

    def make_packed_bank(self, root: Path) -> Path:
        """Recent progress sized to pack the v1 render near the cap, like the
        incident workspace, so only a reserved Members block still fits."""
        bank = self.make_small_bank(root)
        overhead = len(build_start_context(bank).encode("utf-8"))
        recent_heading = len("\n\n## Recent Progress\n\n".encode("utf-8"))
        target = 7650 - overhead - recent_heading - 4
        shares = (0.05, 0.475, 0.475)
        entries = []
        for index, share in enumerate(shares, start=1):
            marker = f"<!-- mb-lite-entry:20261122T120000Z-0000000{index} -->"
            heading = f"### 2026-11-22 12:00:00 - session summary {index}"
            body = "p" * max(0, int(target * share) - len(marker) - len(heading) - 2)
            entries.append(f"{marker}\n{heading}\n{body}")
        (bank / "progress.md").write_text("\n\n".join(entries) + "\n", encoding="utf-8")
        return bank

    def member(self, name: str, size: int) -> tuple[str, str]:
        prefix = f"- {name}: "
        return name, "m" * max(0, size - len(prefix))

    def test_packed_bank_reserves_member_budget_with_counted_omission(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_packed_bank(Path(root))
            members = (self.member("alpha", 700), self.member("beta", 700))
            before = {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()}
            output = build_workspace_start_context(bank, members)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("## Members", output)
            self.assertIn("- alpha:", output)
            self.assertIn("- ... 1 member summaries omitted", output)
            self.assertNotIn("- beta:", output)
            self.assertEqual(before, {p: p.read_bytes() for p in bank.rglob("*") if p.is_file()})

    def test_first_member_too_large_degrades_to_counted_notice_only(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_packed_bank(Path(root))
            members = (self.member("alpha", 6000), self.member("beta", 50))
            output = build_workspace_start_context(bank, members)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("## Members", output)
            self.assertIn("- ... 2 member summaries omitted", output)
            self.assertNotIn("alpha", output)
            self.assertNotIn("beta", output)

    def test_shortest_member_is_guaranteed_when_the_first_is_too_large(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_packed_bank(Path(root))
            members = (self.member("alpha", 60), self.member("beta", 7000))
            output = build_workspace_start_context(bank, members)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("- alpha:", output)
            self.assertIn("- ... 1 member summaries omitted", output)
            self.assertNotIn("- beta:", output)

    def test_unpacked_bank_shows_every_member_without_omission(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_small_bank(Path(root))
            members = (self.member("alpha", 700), self.member("beta", 700))
            output = build_workspace_start_context(bank, members)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("- alpha:", output)
            self.assertIn("- beta:", output)
            self.assertNotIn("member summaries omitted", output)

    def test_protected_sections_beat_an_over_promised_member_reserve(self):
        # A single pathological member line reserves more than the cap has to
        # give; protected sections must still start instead of failing closed.
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_small_bank(Path(root))
            members = (self.member("alpha", 8100),)
            output = build_workspace_start_context(bank, members)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertIn("## Members", output)
            self.assertIn("- ... 1 member summaries omitted", output)
            self.assertNotIn("alpha", output)

    def test_workspace_without_members_keeps_placeholder_block(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_small_bank(Path(root))
            output = build_workspace_start_context(bank, ())
            self.assertIn("## Members", output)
            self.assertIn("No member summaries available.", output)

    def test_members_section_returns_every_member_line(self):
        with tempfile.TemporaryDirectory() as root:
            bank = self.make_small_bank(Path(root))
            members = (self.member("alpha", 700), self.member("beta", 700))
            output = build_workspace_start_context(bank, members, "members")
            self.assertIn("- alpha:", output)
            self.assertIn("- beta:", output)


if __name__ == "__main__":
    unittest.main()
