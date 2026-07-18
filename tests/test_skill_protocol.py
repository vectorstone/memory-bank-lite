from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "SKILL.md"
CLI = REPO / "scripts" / "mb.py"


class SkillProtocolTest(unittest.TestCase):
    def test_normal_done_keeps_confirmation_boundary(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")

        self.assertIn("In normal mode:", skill)
        self.assertIn("Ask for explicit confirmation.", skill)
        self.assertIn("require fresh\nconfirmation because the payload changed", skill)

    def test_fast_write_is_explicit_one_shot_skill_behavior(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")

        self.assertIn("Treat `done --yes` as a one-shot fast-write authorization", skill)
        self.assertIn("write without\nconfirmation", skill)
        for phrase in ("`直接写入`", "`无需确认`", "`跳过确认`", "`自动写入`"):
            self.assertIn(phrase, skill)
        self.assertIn("Do not infer fast-write mode from vague requests", skill)
        self.assertIn("never persist it as a preference", skill)
        self.assertIn("Do not show the draft before writing", skill)
        self.assertIn("retry once without asking", skill)
        self.assertIn("after a second revision conflict", skill)

    def test_yes_is_not_added_to_python_cli(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        cli = CLI.read_text(encoding="utf-8")

        self.assertIn("`--yes` is a skill-level argument; never pass\nit to `mb.py`", skill)
        self.assertNotIn('add_argument("--yes"', cli)


if __name__ == "__main__":
    unittest.main()
