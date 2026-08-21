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

from .attention import AttentionEntry, next_attention_id, parse_attention, render_attention
from .bank import CORE_FILES, validate_bank, validate_v2_bank
from .bank_fs import bank_markdown_files, read_bank_file, read_bank_text
from .errors import MbError
from .security import forbidden_evidence_path


def bank_revision(bank: Path) -> str:
    validate_bank(bank)
    digest = sha256()
    for name in CORE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(read_bank_file(bank, name, required=True) or b"")
        digest.update(b"\0")
    return digest.hexdigest()


def v2_bank_revision(bank: Path) -> str:
    validate_v2_bank(bank)
    digest = sha256()
    root_files = []
    for path in Path(bank).iterdir():
        if path.name.endswith(".md") and not path.is_symlink() and path.is_file():
            root_files.append(path.name)
    sources: list[tuple[str, bytes]] = []
    for name in sorted(root_files):
        sources.append((name, read_bank_file(bank, name, required=True) or b""))
    for directory in ("notes", "plans"):
        for name, source in bank_markdown_files(bank, directory):
            sources.append((f"{directory}/{name}", source.encode("utf-8")))
    for name, content in sorted(sources):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
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
class AttentionChange:
    action: str
    attention_id: str | None
    title: str | None
    scope: str | None
    severity: str | None
    reason: str | None
    evidence: str | None
    verified_at: str | None
    review_after_days: int | None


def _attention_changes(data: dict[str, Any]) -> tuple[AttentionChange, ...]:
    raw = data.get("attention_changes", [])
    if not isinstance(raw, list):
        raise MbError("attention_changes must be a list", 1)
    result: list[AttentionChange] = []
    allowed = {"add", "update", "resolve", "supersede"}
    for value in raw:
        if not isinstance(value, dict) or value.get("action") not in allowed:
            raise MbError("invalid attention change", 1)
        action = str(value["action"])
        attention_id = value.get("attention_id")
        if attention_id is not None and (
            not isinstance(attention_id, str) or not re.fullmatch(r"ATT-[0-9]{3,}", attention_id)
        ):
            raise MbError("invalid attention change ID", 1)
        fields: dict[str, str | None] = {}
        for field in ("title", "scope", "severity", "reason", "evidence", "verified_at"):
            item = value.get(field)
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise MbError(f"invalid attention change {field}", 1)
            fields[field] = item.strip() if isinstance(item, str) else None
        review = value.get("review_after_days")
        if review is not None and (type(review) is not int or review < 1 or review > 3650):
            raise MbError("invalid attention review_after_days", 1)
        if action == "add" and any(fields[field] is None for field in ("title", "scope", "severity", "reason", "evidence", "verified_at")):
            raise MbError("attention add requires all fields", 1)
        if action == "add" and attention_id is not None:
            raise MbError("attention add must not supply an ID", 1)
        if action != "add" and attention_id is None:
            raise MbError(f"attention {action} requires an ID", 1)
        if fields["severity"] is not None and fields["severity"] not in {"high", "medium", "low"}:
            raise MbError("invalid attention severity", 1)
        if fields["verified_at"] is not None:
            try:
                datetime.strptime(fields["verified_at"], "%Y-%m-%d")
            except ValueError as exc:
                raise MbError("invalid attention verified_at", 1) from exc
        result.append(
            AttentionChange(
                action, attention_id, fields["title"], fields["scope"], fields["severity"],
                fields["reason"], fields["evidence"], fields["verified_at"], review,
            )
        )
    return tuple(result)


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
    attention_changes: tuple[AttentionChange, ...] = ()

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
            attention_changes=_attention_changes(data),
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


def _unchecked_items(source: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in re.findall(r"(?m)^\s*[-*]\s+\[\s\]\s+(.+?)\s*$", source)
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
    # A Bank may legitimately have no open checklist items. In that state,
    # completed_items cannot mutate the checklist, so treat it as a no-op and
    # continue applying the independent summary/note/attention/progress data.
    # Once any unchecked item exists, retain the strict exact-one-match rule.
    if _unchecked_items(result):
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


@dataclass(frozen=True)
class PreparedDone:
    outputs: tuple[tuple[Path, str], ...]
    note_path: str | None


class BankWriteError(MbError):
    def __init__(self, message: str, rollback_complete: bool) -> None:
        super().__init__(message, 4)
        self.rollback_complete = rollback_complete


@contextmanager
def _bank_lock(bank: Path) -> Iterator[None]:
    lock_path = bank / ".mb-lite.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise MbError(f"Bank lock acquisition failed: {exc}", 5) from exc
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
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


def _replace_staged_file(path: Path, staged_name: str) -> None:
    os.replace(staged_name, path)


def _restore_file(path: Path, original: bytes | None) -> None:
    if original is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(path.parent)
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
        _replace_staged_file(path, name)
        _fsync_directory(path.parent)
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
        if Path(value).is_absolute():
            raise MbError(f"key file must be repository-relative: {value}", 1)
        if forbidden_evidence_path(value):
            raise MbError(f"key file is a forbidden sensitive input: {value}", 1)
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MbError(f"key file is outside repository: {value}", 1) from exc


def _apply_attention_changes(source: str, changes: tuple[AttentionChange, ...]) -> str:
    if not changes:
        return source
    entries = list(parse_attention(source))
    by_id = {entry.attention_id: index for index, entry in enumerate(entries)}
    for change in changes:
        if change.action == "add":
            attention_id = next_attention_id(tuple(entries))
            from datetime import date
            entry = AttentionEntry(
                attention_id,
                change.title or "",
                change.scope or "",
                change.severity or "medium",
                "active",
                change.reason or "",
                change.evidence or "",
                date.fromisoformat(change.verified_at or ""),
                change.review_after_days or 90,
            )
            by_id[attention_id] = len(entries)
            entries.append(entry)
            continue
        assert change.attention_id is not None
        index = by_id.get(change.attention_id)
        if index is None:
            raise MbError(f"unknown attention ID: {change.attention_id}", 1)
        old = entries[index]
        from datetime import date
        status = old.status
        if change.action == "resolve":
            status = "resolved"
        elif change.action == "supersede":
            status = "superseded"
        entries[index] = AttentionEntry(
            old.attention_id,
            change.title or old.title,
            change.scope or old.scope,
            change.severity or old.severity,
            status,
            change.reason or old.reason,
            change.evidence or old.evidence,
            date.fromisoformat(change.verified_at) if change.verified_at else old.verified_at,
            change.review_after_days or old.review_after_days,
        )
    return render_attention(source, tuple(entries))


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
            _replace_staged_file(path, staged_name)
            applied.append(path)
            _fsync_directory(path.parent)
        staged.clear()
    except OSError as exc:
        rollback_failed = False
        for path in reversed(applied if "applied" in locals() else []):
            try:
                _restore_file(path, originals[path])
            except OSError:
                rollback_failed = True
        detail = "unable to write bank"
        if rollback_failed:
            detail += "; rollback durability could not be confirmed"
        raise BankWriteError(f"{detail}: {exc}", not rollback_failed) from exc
    finally:
        for _, staged_name in staged:
            if os.path.exists(staged_name):
                os.unlink(staged_name)


def prepare_done(bank: Path, repo: Path, payload: DonePayload, now: datetime) -> PreparedDone:
    validate_bank(bank)
    _validate_key_files(repo, payload.key_files)
    timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status_path = bank / "status.md"
    checklist_path = bank / "checklist.md"
    progress_path = bank / "progress.md"
    status = render_status(
        read_bank_text(bank, "status.md", required=True) or "",
        payload,
    )
    checklist = render_checklist(
        read_bank_text(bank, "checklist.md", required=True) or "",
        payload,
    )
    progress_source = read_bank_text(bank, "progress.md", required=True) or ""
    separator = "" if progress_source.endswith("\n") else "\n"
    progress = progress_source + separator + "\n" + render_progress_entry(payload, timestamp)
    attention_path = bank / "attention.md"
    attention_content: str | None = None
    if payload.attention_changes:
        attention_source = read_bank_text(bank, "attention.md", required=True) or ""
        attention_content = _apply_attention_changes(attention_source, payload.attention_changes)
    note_relative: str | None = None
    note_target: Path | None = None
    note_content: str | None = None
    if payload.note is not None:
        note_relative = f"notes/{now:%Y-%m-%d}_{_slug(payload.note.title)}.md"
        note_target = bank / note_relative
        notes_directory = note_target.parent
        if notes_directory.is_symlink():
            raise MbError(f"notes directory is not a real directory: {notes_directory}", 4)
        if note_target.is_symlink():
            raise MbError(f"note already exists: {note_relative}", 4)
        if note_target.exists():
            raise MbError(f"note already exists: {note_relative}", 1)
        note_content = f"# {payload.note.title}\n\n{payload.note.body.rstrip()}\n"
    outputs = [(status_path, status), (checklist_path, checklist), (progress_path, progress)]
    if note_target is not None and note_content is not None:
        outputs.append((note_target, note_content))
    if attention_content is not None:
        outputs.append((attention_path, attention_content))
    return PreparedDone(tuple(outputs), note_relative)


def apply_done(
    bank: Path,
    repo: Path,
    payload: DonePayload,
    now: datetime,
    *,
    revision_function=bank_revision,
) -> ApplyResult:
    validate_bank(bank)
    _validate_key_files(repo, payload.key_files)
    with _bank_lock(bank):
        current_revision = revision_function(bank)
        if current_revision != payload.bank_revision:
            raise MbError("Bank revision changed; regenerate the done draft", 3)
        prepared = prepare_done(bank, repo, payload, now)
        for path, _ in prepared.outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
        _write_all_or_rollback(prepared.outputs)
        return ApplyResult(revision_function(bank), prepared.note_path)
