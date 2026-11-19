"""Distill stable lessons from progress entries into lessons.md.

Distillation is the human-confirmed counterpart to automatic rolling: the
agent drafts lessons with their source entry markers, the user confirms, and
the lessons land in a managed block in ``lessons.md`` where recall weights
them above raw progress.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
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
LESSON_ID_PATTERN = re.compile(r"LSN-[0-9]{3,}")
LESSON_SEQ_RE = re.compile(r"<!-- mb-lite-lesson-seq:([0-9]+) -->")
STORED_LESSON_RE = re.compile(
    r"(?ms)^<!-- mb-lite-lesson:(LSN-[0-9]{3,}) -->\s*"
    r"###\s+LSN-[0-9]{3,}\s+(.+?)[ \t]*\r?$\s*"
    r".*?(?=^<!-- mb-lite-lesson:|^###\s+LSN-[0-9]{3,}\s|^#{1,2}\s"
    r"|^<!-- mb-lite:lessons:end -->|\Z)"
)
LESSON_TITLE_RE = re.compile(r"(?m)^###\s+(LSN-[0-9]{3,})\s+(.+?)\s*$")
NEAR_DUPLICATE_RATIO = 0.85


@dataclass(frozen=True)
class Lesson:
    title: str
    body: str
    sources: tuple[str, ...]
    replaces: str | None = None


@dataclass(frozen=True)
class DistillPayload:
    bank_revision: str
    lessons: tuple[Lesson, ...]
    retire: tuple[str, ...] = ()

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
        raw_lessons = data.get("lessons", [])
        if not isinstance(raw_lessons, list):
            raise MbError("distill payload lessons must be a list", 1)
        raw_retire = data.get("retire", [])
        if not isinstance(raw_retire, list):
            raise MbError("distill payload retire must be a list of lesson ids", 1)
        retire: list[str] = []
        for value in raw_retire:
            if not isinstance(value, str) or not LESSON_ID_PATTERN.fullmatch(value.strip()):
                raise MbError(f"invalid lesson id: {value}", 1)
            retire.append(value.strip())
        if len(set(retire)) != len(retire):
            raise MbError("distill payload retire contains duplicate lesson ids", 1)
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
            if any(
                marker in title or marker in body
                for marker in (LESSONS_START, LESSONS_END, "<!-- mb-lite-lesson:")
            ):
                raise MbError(
                    "lesson title and body must not contain managed-block markers", 1
                )
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
            replaces = value.get("replaces")
            if replaces is not None:
                if not isinstance(replaces, str) or not LESSON_ID_PATTERN.fullmatch(replaces.strip()):
                    raise MbError(f"invalid replacement lesson id: {replaces}", 1)
                replaces = replaces.strip()
            lessons.append(Lesson(title.strip(), body.strip(), tuple(sources), replaces))
        if not lessons and not retire:
            raise MbError("distill payload requires lessons or retire", 1)
        return cls(revision, tuple(lessons), tuple(retire))


@dataclass(frozen=True)
class StoredLesson:
    lesson_id: str
    title: str
    segment: str
    managed: bool = True


def _stored_lessons(source: str) -> list[StoredLesson]:
    if LESSONS_START not in source or LESSONS_END not in source:
        return []
    start = source.index(LESSONS_START) + len(LESSONS_START)
    end = source.index(LESSONS_END)
    return [
        StoredLesson(match.group(1), match.group(2).strip(), match.group(0))
        for match in STORED_LESSON_RE.finditer(source[start:end])
    ]


def _all_lessons(source: str) -> list[StoredLesson]:
    """Every LSN heading in the file, including hand-written unmanaged ones."""
    managed = {lesson.lesson_id: lesson for lesson in _stored_lessons(source)}
    result: list[StoredLesson] = []
    for match in LESSON_TITLE_RE.finditer(source):
        lesson_id = match.group(1)
        result.append(
            managed.get(
                lesson_id,
                StoredLesson(lesson_id, match.group(2).strip(), "", False),
            )
        )
    return result


def _normalized_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _title_similarity(left: str, right: str) -> float:
    first, second = _normalized_title(left), _normalized_title(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    return SequenceMatcher(None, first, second).ratio()


def _check_lesson_titles(lessons: tuple[Lesson, ...], remaining: list[StoredLesson]) -> None:
    for index, lesson in enumerate(lessons):
        for stored in remaining:
            if _title_similarity(lesson.title, stored.title) < NEAR_DUPLICATE_RATIO:
                continue
            if _normalized_title(lesson.title) == _normalized_title(stored.title):
                override = (
                    f"set \"replaces\": \"{stored.lesson_id}\" to override it"
                    if stored.managed
                    else "merge it or edit lessons.md manually"
                )
                raise MbError(
                    f"lesson already exists: {stored.lesson_id} {stored.title}; "
                    f"{override}",
                    3,
                )
            override = (
                f"merge it or set \"replaces\": \"{stored.lesson_id}\""
                if stored.managed
                else "merge it or edit lessons.md manually"
            )
            raise MbError(
                f"lesson {lesson.title!r} is too similar to existing "
                f"{stored.lesson_id} ({stored.title!r}); {override}",
                3,
            )
        for other in lessons[:index]:
            if _title_similarity(lesson.title, other.title) >= NEAR_DUPLICATE_RATIO:
                raise MbError(
                    f"lesson titles are too similar within the payload: "
                    f"{other.title!r} and {lesson.title!r}",
                    1,
                )


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
    lessons_source = read_bank_text(bank, "lessons.md") or ""
    stored = _stored_lessons(lessons_source)
    return {
        "bank_path": str(bank),
        "bank_revision": v2_bank_revision(bank),
        "lessons_file_present": read_bank_text(bank, "lessons.md") is not None,
        "lesson_count": len(stored),
        "lessons": [
            {"id": lesson.lesson_id, "title": lesson.title} for lesson in stored
        ],
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


def _rewrite_block(source: str, retired: set[str], additions: str) -> str:
    start = source.index(LESSONS_START)
    end = source.index(LESSONS_END, start)
    inner = source[start + len(LESSONS_START):end]
    pieces: list[str] = []
    cursor = 0
    for match in STORED_LESSON_RE.finditer(inner):
        # Re-emit unmatched text verbatim so manual edits inside the block are
        # never silently deleted.
        pieces.append(inner[cursor:match.start()])
        if match.group(1) not in retired:
            pieces.append(match.group(0))
        cursor = match.end()
    pieces.append(inner[cursor:])
    body = "".join(pieces).rstrip("\n")
    if additions:
        body = (body + "\n\n" if body else "") + additions
    new_inner = body + "\n" if body else "\n"
    return source[:start] + LESSONS_START + new_inner + LESSONS_END + source[end + len(LESSONS_END):]


def _stamp_sequence(content: str, value: int) -> str:
    """Persist the highest lesson id ever issued so retirement never recycles ids."""
    marker = f"<!-- mb-lite-lesson-seq:{value:03d} -->"
    position = content.index(LESSONS_START) + len(LESSONS_START)
    end = content.index(LESSONS_END, position)
    block = content[position:end]
    boundary = block.find("<!-- mb-lite-lesson:")
    header, tail = (block, "") if boundary < 0 else (block[:boundary], block[boundary:])
    if LESSON_SEQ_RE.search(header):
        header = LESSON_SEQ_RE.sub(marker, header, count=1)
    else:
        header = f"\n{marker}" + header
    return content[:position] + header + tail + content[end:]


def apply_distill(
    bank: Path,
    payload: DistillPayload,
    *,
    revision_function=v2_bank_revision,
) -> dict[str, Any]:
    bank = validate_v2_bank(Path(bank))
    if not payload.lessons and not payload.retire:
        raise MbError("distill payload requires lessons or retire", 1)
    with _bank_lock(bank):
        if revision_function(bank) != payload.bank_revision:
            raise MbError("Bank revision changed; regenerate the distill draft", 3)
        known_markers = {entry.marker for _, entry in _source_entries(bank)}
        for lesson in payload.lessons:
            for marker in lesson.sources:
                if marker not in known_markers:
                    raise MbError(f"unknown source entry marker: {marker}", 1)
        source = read_bank_text(bank, "lessons.md")
        all_source = source or ""
        all_lessons = _all_lessons(all_source)
        managed_ids = {lesson.lesson_id for lesson in all_lessons if lesson.managed}
        retire_ids = set(payload.retire) | {
            lesson.replaces for lesson in payload.lessons if lesson.replaces
        }
        unknown = sorted(
            retire_ids - {lesson.lesson_id for lesson in all_lessons}
        )
        if unknown:
            raise MbError(f"unknown lesson id: {', '.join(unknown)}", 1)
        unmanaged = sorted(retire_ids - managed_ids)
        if unmanaged:
            raise MbError(
                f"lesson is not managed: {', '.join(unmanaged)}; edit lessons.md manually",
                1,
            )
        remaining = [
            lesson for lesson in all_lessons if lesson.lesson_id not in retire_ids
        ]
        _check_lesson_titles(payload.lessons, remaining)
        sequence = max(
            (int(match.group(1)) for match in LESSON_SEQ_RE.finditer(all_source)),
            default=0,
        )
        highest = max(
            sequence,
            max(
                (int(lesson.lesson_id.split("-")[1]) for lesson in all_lessons),
                default=0,
            ),
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
            if source.index(LESSONS_START) >= source.index(LESSONS_END):
                raise MbError("lessons.md managed block markers are out of order", 4)
            content = _rewrite_block(source, retire_ids, additions)
        else:
            if LESSONS_END in source:
                raise MbError("lessons.md has an end marker without a start marker", 4)
            heading = re.search(r"(?m)^#\s+.+$", source)
            block = (
                f"{LESSONS_START}\n## Verified Lessons\n\n{additions}\n{LESSONS_END}\n"
            )
            if heading is None:
                content = f"# Lessons\n\n{block}\n{source}"
            else:
                position = heading.end()
                content = source[:position] + "\n\n" + block + source[position:]
        content = _stamp_sequence(content, highest + len(payload.lessons))
        _write_all_or_rollback(((bank / "lessons.md", content),))
        return {
            "bank_path": str(bank),
            "bank_revision": revision_function(bank),
            "lessons_added": len(payload.lessons),
            "lesson_ids": ids,
            "lessons_retired": len(retire_ids),
            "retired_ids": sorted(retire_ids),
            "lessons_path": "lessons.md",
        }
