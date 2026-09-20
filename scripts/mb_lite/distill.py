"""Distill stable lessons from progress entries into lessons.md.

Distillation is the human-confirmed counterpart to automatic rolling: the
agent drafts lessons with their source entry markers, the user confirms, and
the lessons land in a managed block in ``lessons.md`` where recall weights
them above raw progress.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .archive import ENTRY_RE, ProgressEntry, split_progress
from .bank import validate_v2_bank
from .bank_fs import bank_markdown_files, read_bank_text
from .done import _bank_lock, _write_all_or_rollback, v2_bank_revision
from .errors import MbError

LESSONS_START = "<!-- mb-lite:lessons:start -->"
LESSONS_END = "<!-- mb-lite:lessons:end -->"
LESSON_ID_RE = re.compile(r"<!-- mb-lite-lesson:(LSN-[0-9]{3,}) -->")
LESSON_TITLE_RE = re.compile(r"(?m)^###\s+LSN-[0-9]{3,}\s+(.+?)\s*$")


@dataclass(frozen=True)
class Lesson:
    title: str
    body: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class DistillPayload:
    bank_revision: str
    lessons: tuple[Lesson, ...]

    @classmethod
    def from_json(cls, raw: str) -> "DistillPayload":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MbError("distill payload must be valid JSON", 1) from exc
        if not isinstance(data, dict):
            raise MbError("distill payload must be a JSON object", 1)
        revision = data.get("bank_revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise MbError("bank_revision must be a lowercase SHA-256", 1)
        raw_lessons = data.get("lessons")
        if not isinstance(raw_lessons, list) or not raw_lessons:
            raise MbError("distill payload requires lessons", 1)
        lessons: list[Lesson] = []
        seen_titles: set[str] = set()
        for value in raw_lessons:
            if not isinstance(value, dict):
                raise MbError("each lesson must be an object", 1)
            title = value.get("title")
            body = value.get("body")
            if not isinstance(title, str) or not title.strip() or "\n" in title:
                raise MbError("lesson title must be a non-empty single line", 1)
            if not isinstance(body, str) or not body.strip():
                raise MbError("lesson body must be non-empty", 1)
            if title.strip() in seen_titles:
                raise MbError(f"duplicate lesson title: {title.strip()}", 1)
            seen_titles.add(title.strip())
            sources_raw = value.get("sources", [])
            if not isinstance(sources_raw, list):
                raise MbError("lesson sources must be a list of entry markers", 1)
            sources: list[str] = []
            for marker in sources_raw:
                if not isinstance(marker, str) or not ENTRY_RE.fullmatch(marker.strip()):
                    raise MbError(f"invalid entry marker: {marker}", 1)
                sources.append(marker.strip())
            lessons.append(Lesson(title.strip(), body.strip(), tuple(sources)))
        return cls(revision, tuple(lessons))


def _source_entries(bank: Path) -> list[tuple[str, ProgressEntry]]:
    result: list[tuple[str, ProgressEntry]] = []
    progress = read_bank_text(bank, "progress.md", required=True) or ""
    _, entries = split_progress(progress)
    result.extend(("progress.md", entry) for entry in entries)
    for name, content in bank_markdown_files(bank, "archive"):
        if not name.startswith("progress-"):
            continue
        _, archive_entries = split_progress(content)
        result.extend((f"archive/{name}", entry) for entry in archive_entries)
    return result


def distill_snapshot(bank: Path) -> dict[str, Any]:
    bank = validate_v2_bank(Path(bank))
    entries = _source_entries(bank)
    return {
        "bank_path": str(bank),
        "bank_revision": v2_bank_revision(bank),
        "lessons_file_present": read_bank_text(bank, "lessons.md") is not None,
        "lesson_count": len(LESSON_ID_RE.findall(read_bank_text(bank, "lessons.md") or "")),
        "entries": [
            {"source": source, "marker": entry.marker, "title": entry.title}
            for source, entry in entries
        ],
    }


def _render_lesson(lesson_id: str, lesson: Lesson) -> str:
    lines = [
        f"<!-- mb-lite-lesson:{lesson_id} -->",
        f"### {lesson_id} {lesson.title}",
        "",
        lesson.body,
    ]
    if lesson.sources:
        joined = ", ".join(f"`{marker}`" for marker in lesson.sources)
        lines.extend(["", f"- Sources: {joined}"])
    return "\n".join(lines)


def apply_distill(
    bank: Path,
    payload: DistillPayload,
    *,
    revision_function=v2_bank_revision,
) -> dict[str, Any]:
    bank = validate_v2_bank(Path(bank))
    if not payload.lessons:
        raise MbError("distill payload requires lessons", 1)
    with _bank_lock(bank):
        if revision_function(bank) != payload.bank_revision:
            raise MbError("Bank revision changed; regenerate the distill draft", 3)
        known_markers = {entry.marker for _, entry in _source_entries(bank)}
        for lesson in payload.lessons:
            for marker in lesson.sources:
                if marker not in known_markers:
                    raise MbError(f"unknown source entry marker: {marker}", 1)
        source = read_bank_text(bank, "lessons.md")
        existing_titles = set(LESSON_TITLE_RE.findall(source or ""))
        for lesson in payload.lessons:
            if lesson.title in existing_titles:
                raise MbError(f"lesson already exists: {lesson.title}", 3)
        highest = max(
            (int(marker.split("-")[1]) for marker in LESSON_ID_RE.findall(source or "")),
            default=0,
        )
        rendered: list[str] = []
        ids: list[str] = []
        for index, lesson in enumerate(payload.lessons, start=1):
            lesson_id = f"LSN-{highest + index:03d}"
            ids.append(lesson_id)
            rendered.append(_render_lesson(lesson_id, lesson))
        additions = "\n\n".join(rendered)
        if source is None:
            content = (
                f"# Lessons\n\n{LESSONS_START}\n## Verified Lessons\n\n"
                f"{additions}\n{LESSONS_END}\n"
            )
        elif LESSONS_START in source:
            if source.count(LESSONS_START) != 1 or source.count(LESSONS_END) != 1:
                raise MbError("lessons.md must contain exactly one managed block", 4)
            position = source.index(LESSONS_END)
            prefix = source[:position].rstrip("\n")
            content = f"{prefix}\n\n{additions}\n{source[position:]}"
            if not content.endswith("\n"):
                content += "\n"
        else:
            heading = re.search(r"(?m)^#\s+.+$", source)
            block = (
                f"{LESSONS_START}\n## Verified Lessons\n\n{additions}\n{LESSONS_END}\n"
            )
            if heading is None:
                content = f"# Lessons\n\n{block}\n{source}"
            else:
                position = heading.end()
                content = source[:position] + "\n\n" + block + source[position:]
        _write_all_or_rollback(((bank / "lessons.md", content),))
        return {
            "bank_path": str(bank),
            "bank_revision": revision_function(bank),
            "lessons_added": len(payload.lessons),
            "lesson_ids": ids,
            "lessons_path": "lessons.md",
        }
