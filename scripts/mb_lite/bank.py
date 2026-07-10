from pathlib import Path

from .errors import MbError


CORE_FILES = ("status.md", "checklist.md", "progress.md")
OPTIONAL_FILES = ("roadmap.md", "research.md", "backlog.md", "lessons.md")
TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates" / ".memory-bank"


def initialize_bank(bank: Path) -> Path:
    bank = Path(bank)
    bank.mkdir(parents=True, exist_ok=True)
    (bank / "notes").mkdir(exist_ok=True)
    (bank / "plans" / "done").mkdir(parents=True, exist_ok=True)
    for name in CORE_FILES + OPTIONAL_FILES:
        target = bank / name
        if not target.exists():
            target.write_bytes((TEMPLATE_ROOT / name).read_bytes())
    return bank


def validate_bank(bank: Path) -> Path:
    bank = Path(bank)
    if not bank.is_dir():
        raise MbError(f"missing readable status.md: {bank / 'status.md'}", 4)
    for name in CORE_FILES:
        path = bank / name
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise MbError(f"missing readable {name}: {path}", 4) from None
    return bank
