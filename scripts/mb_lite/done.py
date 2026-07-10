from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator

from .bank import CORE_FILES
from .errors import MbError


def bank_revision(bank: Path) -> str:
    digest = sha256()
    for name in CORE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((bank / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _string_list(data: dict[str, Any], field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list):
        raise MbError(f"{field} must contain non-empty strings", 1)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MbError(f"{field} must contain non-empty strings", 1)
        stripped = item.strip()
        if not stripped:
            raise MbError(f"{field} must contain non-empty strings", 1)
        items.append(stripped)
    return tuple(items)


@dataclass(frozen=True)
class NotePayload:
    title: str
    body: str


@dataclass(frozen=True)
class DonePayload:
    bank_revision: str
    summary: str
    decisions: tuple[str, ...]
    next_steps: tuple[str, ...]
    blockers: tuple[str, ...]
    key_files: tuple[str, ...]
    completed_items: tuple[str, ...]
    new_items: tuple[str, ...]
    note: NotePayload | None

    @classmethod
    def from_json(cls, raw: str) -> "DonePayload":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MbError("done payload must be valid JSON", 1) from exc
        if not isinstance(data, dict):
            raise MbError("done payload must be a JSON object", 1)

        bank_revision_value = data.get("bank_revision")
        if not isinstance(bank_revision_value, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            bank_revision_value,
        ):
            raise MbError("bank_revision must be a lowercase SHA-256", 1)

        summary_value = data.get("summary")
        if not isinstance(summary_value, str) or not summary_value.strip():
            raise MbError("summary must be a non-empty string", 1)

        note_value = data.get("note")
        if note_value is None:
            note = None
        elif isinstance(note_value, dict):
            title_value = note_value.get("title")
            body_value = note_value.get("body")
            if not isinstance(title_value, str) or not title_value.strip():
                raise MbError("note title and body must be non-empty", 1)
            if not isinstance(body_value, str) or not body_value.strip():
                raise MbError("note title and body must be non-empty", 1)
            note = NotePayload(
                title=title_value.strip(),
                body=body_value.strip(),
            )
        else:
            raise MbError("note must be null or an object with title and body", 1)

        return cls(
            bank_revision=bank_revision_value,
            summary=summary_value.strip(),
            decisions=_string_list(data, "decisions"),
            next_steps=_string_list(data, "next_steps"),
            blockers=_string_list(data, "blockers"),
            key_files=_string_list(data, "key_files"),
            completed_items=_string_list(data, "completed_items"),
            new_items=_string_list(data, "new_items"),
            note=note,
        )


CURRENT_START = "<!-- mb-lite:current:start -->"
CURRENT_END = "<!-- mb-lite:current:end -->"
ITEMS_START = "<!-- mb-lite:open-items:start -->"
ITEMS_END = "<!-- mb-lite:open-items:end -->"


def _replace_or_insert(
    source: str,
    start: str,
    end: str,
    block: str,
    after_h1: bool,
) -> str:
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end),
        re.DOTALL,
    )
    matches = list(pattern.finditer(source))
    if len(matches) > 1:
        raise MbError(f"multiple managed blocks found for {start}", 4)
    if matches:
        match = matches[0]
        return source[:match.start()] + block + source[match.end():]
    if after_h1:
        heading = re.search(r"(?m)^#\s+.+$", source)
        if heading is None:
            raise MbError(
                "status.md must contain an H1 before inserting current state",
                4,
            )
        position = heading.end()
        return source[:position] + "\n\n" + block + source[position:]
    separator = "" if source.endswith("\n") else "\n"
    return source + separator + "\n" + block + "\n"


def _managed_block(source: str, start: str, end: str) -> re.Match[str] | None:
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end),
        re.DOTALL,
    )
    matches = list(pattern.finditer(source))
    if len(matches) > 1:
        raise MbError(f"multiple managed blocks found for {start}", 4)
    return matches[0] if matches else None


def render_status(source: str, payload: DonePayload) -> str:
    next_value = "; ".join(payload.next_steps) if payload.next_steps else "None"
    blocker_value = "; ".join(payload.blockers) if payload.blockers else "None"
    block = (
        f"{CURRENT_START}\n"
        "## Memory Bank Lite Current\n\n"
        f"**Focus:** {payload.summary}\n"
        f"**Next:** {next_value}\n"
        f"**Blockers:** {blocker_value}\n"
        f"{CURRENT_END}"
    )
    return _replace_or_insert(source, CURRENT_START, CURRENT_END, block, True)


def _unchecked_pattern(item: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?m)^(\s*[-*]\s+)\[\s\](\s+{re.escape(item)}\s*)$",
    )


def _managed_open_items(source: str) -> tuple[str, ...]:
    pattern = re.compile(
        re.escape(ITEMS_START) + r"(.*?)" + re.escape(ITEMS_END),
        re.DOTALL,
    )
    match = pattern.search(source)
    if match is None:
        return ()
    return tuple(
        item.strip()
        for item in re.findall(r"(?m)^\s*[-*]\s+\[\s\]\s+(.+?)\s*$", match.group(1))
    )


def render_checklist(source: str, payload: DonePayload) -> str:
    result = source
    for item in payload.completed_items:
        pattern = _unchecked_pattern(item)
        matches = list(pattern.finditer(result))
        if len(matches) != 1:
            raise MbError(
                f"expected exactly one unchecked item for completion: {item}",
                1,
            )
        result = pattern.sub(r"\1[x]\2", result, count=1)
    existing = set(
        re.findall(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$", result),
    )
    managed = _managed_open_items(result)
    additions = [
        item
        for item in payload.new_items
        if item not in existing and item not in managed
    ]
    managed_block = _managed_block(result, ITEMS_START, ITEMS_END)
    if not additions and managed_block is None:
        return result
    combined = managed + tuple(additions)
    if not combined:
        if managed_block is None:
            return result
        return result[:managed_block.start()] + result[managed_block.end():]
    block = (
        f"{ITEMS_START}\n"
        "## Memory Bank Lite Open Items\n\n"
        + "\n".join(f"- [ ] {item}" for item in combined)
        + f"\n{ITEMS_END}"
    )
    return _replace_or_insert(result, ITEMS_START, ITEMS_END, block, False)


def _bullet_section(
    title: str,
    values: tuple[str, ...],
    code: bool = False,
) -> str:
    if not values:
        return f"## {title}\n- None"
    bullets = [f"- `{value}`" if code else f"- {value}" for value in values]
    return f"## {title}\n" + "\n".join(bullets)


def render_progress_entry(payload: DonePayload, timestamp: str) -> str:
    marker_hash = sha256(
        (timestamp + "\0" + payload.summary).encode("utf-8"),
    ).hexdigest()[:8]
    compact = timestamp.replace("-", "").replace(":", "")
    marker = f"{compact}-{marker_hash}"
    heading_time = timestamp.replace("T", " ").removesuffix("Z")
    sections = [
        f"<!-- mb-lite-entry:{marker} -->",
        f"### {heading_time} - {payload.summary}",
        _bullet_section("Decisions", payload.decisions),
        _bullet_section("Next Steps", payload.next_steps),
        _bullet_section("Blockers", payload.blockers),
        _bullet_section("Key Files", payload.key_files, code=True),
    ]
    return "\n\n".join(sections).rstrip() + "\n"


@dataclass(frozen=True)
class ApplyResult:
    revision: str
    note_path: str | None


@contextmanager
def _bank_lock(bank: Path) -> Iterator[None]:
    lock_path = bank / ".mb-lite.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise MbError(f"Bank lock acquisition failed: {exc}", 5) from exc
        yield


def _stage_content(path: Path, content: str) -> str:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return name


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit_staged_file(path: Path, staged_name: str) -> None:
    os.replace(staged_name, path)
    _fsync_directory(path.parent)


def _restore_file(path: Path, original: bytes | None) -> None:
    if original is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.restore.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        _commit_staged_file(path, name)
        name = ""
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:60] or "memory-note"


def _validate_key_files(repo: Path, values: tuple[str, ...]) -> None:
    root = repo.resolve()
    for value in values:
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MbError(f"key file is outside repository: {value}", 1) from exc


def _write_all_or_rollback(outputs: tuple[tuple[Path, str], ...]) -> None:
    originals = {
        path: path.read_bytes() if path.exists() else None
        for path, _ in outputs
    }
    staged: list[tuple[Path, str]] = []
    try:
        for path, content in outputs:
            staged.append((path, _stage_content(path, content)))
        applied: list[Path] = []
        for path, staged_name in staged:
            _commit_staged_file(path, staged_name)
            applied.append(path)
        staged.clear()
    except OSError as exc:
        for path in reversed(applied if "applied" in locals() else []):
            _restore_file(path, originals[path])
        raise MbError(f"unable to write bank: {exc}", 4) from exc
    finally:
        for _, staged_name in staged:
            if os.path.exists(staged_name):
                os.unlink(staged_name)


def apply_done(bank: Path, repo: Path, payload: DonePayload, now: datetime) -> ApplyResult:
    _validate_key_files(repo, payload.key_files)
    timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _bank_lock(bank):
        current_revision = bank_revision(bank)
        if current_revision != payload.bank_revision:
            raise MbError("Bank revision changed; regenerate the done draft", 3)
        status_path = bank / "status.md"
        checklist_path = bank / "checklist.md"
        progress_path = bank / "progress.md"
        status = render_status(status_path.read_text(encoding="utf-8"), payload)
        checklist = render_checklist(
            checklist_path.read_text(encoding="utf-8"),
            payload,
        )
        progress_source = progress_path.read_text(encoding="utf-8")
        separator = "" if progress_source.endswith("\n") else "\n"
        progress = (
            progress_source
            + separator
            + "\n"
            + render_progress_entry(payload, timestamp)
        )
        note_relative: str | None = None
        note_target: Path | None = None
        note_content: str | None = None
        if payload.note is not None:
            note_relative = f"notes/{now:%Y-%m-%d}_{_slug(payload.note.title)}.md"
            note_target = bank / note_relative
            if note_target.exists():
                raise MbError(f"note already exists: {note_relative}", 1)
            note_target.parent.mkdir(parents=True, exist_ok=True)
            note_content = f"# {payload.note.title}\n\n{payload.note.body.rstrip()}\n"
        outputs = [
            (status_path, status),
            (checklist_path, checklist),
            (progress_path, progress),
        ]
        if note_target is not None and note_content is not None:
            outputs.append((note_target, note_content))
        _write_all_or_rollback(tuple(outputs))
        return ApplyResult(bank_revision(bank), note_relative)
