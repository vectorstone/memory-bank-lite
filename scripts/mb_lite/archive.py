"""Rolling archive helpers for progress entries and attention items."""

from dataclasses import dataclass
import re

PROGRESS_MAX_ENTRIES = 15
PROGRESS_MAX_BYTES = 32768

ENTRY_RE = re.compile(r"<!-- mb-lite-entry:([0-9]{8}T[0-9]{6}Z)-[0-9a-f]{8} -->")
TITLE_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")


@dataclass(frozen=True)
class ProgressEntry:
    marker_date: str
    raw: str

    @property
    def marker(self) -> str:
        match = ENTRY_RE.match(self.raw.lstrip())
        if match is None:
            return ""
        return match.group(0)

    @property
    def title(self) -> str:
        match = TITLE_RE.search(self.raw)
        return match.group(1) if match else self.marker_date

    @property
    def year(self) -> str:
        return self.marker_date[:4]


@dataclass(frozen=True)
class ProgressRoll:
    retained: str
    archived: tuple[ProgressEntry, ...]

    @property
    def archived_count(self) -> int:
        return len(self.archived)


def split_progress(source: str) -> tuple[str, tuple[ProgressEntry, ...]]:
    matches = list(ENTRY_RE.finditer(source))
    if not matches:
        return source, ()
    preamble = source[: matches[0].start()]
    entries: list[ProgressEntry] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        entries.append(ProgressEntry(match.group(1)[:8], source[match.start():end]))
    return preamble, tuple(entries)


def plan_progress_roll(
    source: str,
    *,
    max_entries: int = PROGRESS_MAX_ENTRIES,
    max_bytes: int = PROGRESS_MAX_BYTES,
) -> ProgressRoll:
    """Keep the newest entries within both limits; the rest roll verbatim.

    Entries are moved byte-for-byte, never summarized, so no information is
    lost (deliberate: avoid type-blind compaction).
    """
    preamble, entries = split_progress(source)
    kept = list(entries)
    rolled: list[ProgressEntry] = []
    while len(kept) > 1 and (
        len(kept) > max_entries
        or sum(len(entry.raw.encode("utf-8")) for entry in kept) > max_bytes
    ):
        rolled.append(kept.pop(0))
    retained = preamble + "".join(entry.raw for entry in kept)
    if retained and not retained.endswith("\n"):
        retained += "\n"
    return ProgressRoll(retained, tuple(rolled))


def progress_archive_name(entry: ProgressEntry) -> str:
    return f"archive/progress-{entry.year}.md"


def merge_archive_text(existing: str | None, additions: tuple[str, ...], header: str) -> str:
    if existing is None:
        return header + "\n\n" + "".join(additions)
    if existing.endswith("\n\n"):
        separator = ""
    elif existing.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    return existing + separator + "".join(additions)
