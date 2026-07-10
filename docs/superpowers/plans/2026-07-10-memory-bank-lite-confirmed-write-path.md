# Memory Bank Lite Confirmed Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-phase `done` action that drafts in conversation, writes only after explicit confirmation, preserves legacy Markdown, and rejects concurrent revisions.

**Architecture:** The skill owns draft generation and confirmation; the CLI owns revision calculation, payload validation, locking, and deterministic file transformation. `done --apply` stages complete new file bodies under one Bank lock, rechecks the revision, then atomically replaces each affected file while preserving every legacy byte outside Lite-managed blocks.

**Tech Stack:** Python 3.11+ standard library, `unittest`, JSON over stdin, Markdown marker blocks, `fcntl` locks, SHA-256.

---

## Prerequisite

Complete `2026-07-10-memory-bank-lite-core-read-path.md` first. This plan relies
on `MbError`, `RegistryStore`, `validate_bank`, and the `scripts/mb.py` dispatcher.

## File Structure

- Create `scripts/mb_lite/done.py`: payload schema, revision, compatible transforms, locking, and apply.
- Create `tests/test_done_payload.py`: schema and path-validation contracts.
- Create `tests/test_done_transform.py`: status/checklist/progress compatibility contracts.
- Create `tests/test_done_apply.py`: confirmation boundary, conflict, note, and concurrency contracts.
- Modify `scripts/mb.py`: add `done --revision` and `done --apply`.
- Modify `SKILL.md`: define the exact draft, confirmation, and apply protocol.

### Task 1: Done Payload And Bank Revision

**Files:**
- Create: `scripts/mb_lite/done.py`
- Create: `tests/test_done_payload.py`

- [ ] **Step 1: Write failing payload and revision tests**

```python
from pathlib import Path
import json
import tempfile
import unittest

from scripts.mb_lite.done import DonePayload, bank_revision
from scripts.mb_lite.errors import MbError


class DonePayloadTest(unittest.TestCase):
    def test_revision_changes_when_any_core_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            for name in ("status.md", "checklist.md", "progress.md"):
                (bank / name).write_text(name, encoding="utf-8")
            first = bank_revision(bank)
            (bank / "checklist.md").write_text("changed", encoding="utf-8")
            self.assertNotEqual(bank_revision(bank), first)

    def test_payload_requires_fixed_field_types(self) -> None:
        raw = json.dumps({
            "bank_revision": "a" * 64,
            "summary": "Implemented registry",
            "decisions": ["Use one central registry"],
            "next_steps": ["Add start renderer"],
            "blockers": [],
            "key_files": ["scripts/mb_lite/registry.py"],
            "completed_items": ["Define registry schema"],
            "new_items": ["Implement recall"],
            "note": None,
        })
        payload = DonePayload.from_json(raw)
        self.assertEqual(payload.summary, "Implemented registry")

    def test_payload_rejects_wrong_list_item_type(self) -> None:
        raw = json.dumps({
            "bank_revision": "a" * 64,
            "summary": "Implemented registry",
            "decisions": [7],
            "next_steps": [],
            "blockers": [],
            "key_files": [],
            "completed_items": [],
            "new_items": [],
            "note": None,
        })
        with self.assertRaisesRegex(MbError, "decisions must contain non-empty strings"):
            DonePayload.from_json(raw)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_done_payload -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.done'`.

- [ ] **Step 3: Implement payload parsing and revision hashing**

Create the first part of `scripts/mb_lite/done.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .errors import MbError


CORE_FILES = ("status.md", "checklist.md", "progress.md")


def bank_revision(bank: Path) -> str:
    digest = sha256()
    for name in CORE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((bank / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _strings(data: dict[str, Any], field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise MbError(f"{field} must contain non-empty strings", 1)
    return tuple(item.strip() for item in value)


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
        revision = data.get("bank_revision")
        summary = data.get("summary")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise MbError("bank_revision must be a lowercase SHA-256", 1)
        if not isinstance(summary, str) or not summary.strip():
            raise MbError("summary must be a non-empty string", 1)
        raw_note = data.get("note")
        if raw_note is None:
            note = None
        elif isinstance(raw_note, dict) and isinstance(raw_note.get("title"), str) and isinstance(raw_note.get("body"), str):
            title = raw_note["title"].strip()
            body = raw_note["body"].strip()
            if not title or not body:
                raise MbError("note title and body must be non-empty", 1)
            note = NotePayload(title, body)
        else:
            raise MbError("note must be null or an object with title and body", 1)
        return cls(
            revision,
            summary.strip(),
            _strings(data, "decisions"),
            _strings(data, "next_steps"),
            _strings(data, "blockers"),
            _strings(data, "key_files"),
            _strings(data, "completed_items"),
            _strings(data, "new_items"),
            note,
        )
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_done_payload -v`

Expected: `Ran 3 tests` and `OK`.

- [ ] **Step 5: Commit payload and revision contract**

```bash
git add scripts/mb_lite/done.py tests/test_done_payload.py
git commit -m "feat: define confirmed done payload"
```

### Task 2: Compatible Markdown Transforms

**Files:**
- Modify: `scripts/mb_lite/done.py`
- Create: `tests/test_done_transform.py`

- [ ] **Step 1: Write failing transform tests**

```python
import unittest

from scripts.mb_lite.done import (
    DonePayload,
    render_checklist,
    render_progress_entry,
    render_status,
)
from scripts.mb_lite.errors import MbError


PAYLOAD = DonePayload.from_json("""{
  "bank_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "summary": "Implemented registry",
  "decisions": ["Use one central registry"],
  "next_steps": ["Implement recall"],
  "blockers": [],
  "key_files": ["scripts/mb_lite/registry.py"],
  "completed_items": ["Define registry schema"],
  "new_items": ["Implement recall"],
  "note": null
}""")


class DoneTransformTest(unittest.TestCase):
    def test_status_replaces_only_lite_block(self) -> None:
        source = "# Status\nuser-before\n<!-- mb-lite:current:start -->\nold\n<!-- mb-lite:current:end -->\nuser-after\n"
        result = render_status(source, PAYLOAD)
        self.assertTrue(result.startswith("# Status\nuser-before\n"))
        self.assertTrue(result.endswith("user-after\n"))
        self.assertIn("**Focus:** Implemented registry", result)
        self.assertIn("**Next:** Implement recall", result)

    def test_checklist_completes_exact_item_and_adds_new_item(self) -> None:
        source = "# Checklist\n- [ ] Define registry schema\n- [ ] Keep exact\n"
        result = render_checklist(source, PAYLOAD)
        self.assertIn("- [x] Define registry schema", result)
        self.assertIn("- [ ] Keep exact", result)
        self.assertIn("- [ ] Implement recall", result)

    def test_checklist_ambiguous_completion_fails(self) -> None:
        source = "- [ ] Define registry schema\n- [ ] Define registry schema\n"
        with self.assertRaisesRegex(MbError, "expected exactly one unchecked item"):
            render_checklist(source, PAYLOAD)

    def test_progress_entry_has_stable_marker_and_fixed_sections(self) -> None:
        result = render_progress_entry(PAYLOAD, "2026-07-10T12:00:00Z")
        self.assertRegex(result, r"<!-- mb-lite-entry:[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8} -->")
        self.assertIn("## Decisions\n- Use one central registry", result)
        self.assertIn("## Key Files\n- `scripts/mb_lite/registry.py`", result)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_done_transform -v`

Expected: import errors for `render_status`, `render_checklist`, and `render_progress_entry`.

- [ ] **Step 3: Implement complete compatible transforms**

Append to `scripts/mb_lite/done.py`:

```python
CURRENT_START = "<!-- mb-lite:current:start -->"
CURRENT_END = "<!-- mb-lite:current:end -->"
ITEMS_START = "<!-- mb-lite:open-items:start -->"
ITEMS_END = "<!-- mb-lite:open-items:end -->"


def _replace_or_insert(source: str, start: str, end: str, block: str, after_h1: bool) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    matches = list(pattern.finditer(source))
    if len(matches) > 1:
        raise MbError(f"multiple managed blocks found for {start}", 4)
    if matches:
        match = matches[0]
        return source[:match.start()] + block + source[match.end():]
    if after_h1:
        heading = re.search(r"(?m)^#\s+.+$", source)
        if not heading:
            raise MbError("status.md must contain an H1 before inserting current state", 4)
        position = heading.end()
        return source[:position] + "\n\n" + block + source[position:]
    separator = "" if source.endswith("\n") else "\n"
    return source + separator + "\n" + block + "\n"


def render_status(source: str, payload: DonePayload) -> str:
    next_value = "; ".join(payload.next_steps) if payload.next_steps else "None"
    blocker_value = "; ".join(payload.blockers) if payload.blockers else "None"
    block = (
        f"{CURRENT_START}\n## Memory Bank Lite Current\n\n"
        f"**Focus:** {payload.summary}\n"
        f"**Next:** {next_value}\n"
        f"**Blockers:** {blocker_value}\n{CURRENT_END}"
    )
    return _replace_or_insert(source, CURRENT_START, CURRENT_END, block, True)


def _unchecked_pattern(item: str) -> re.Pattern[str]:
    return re.compile(rf"(?m)^(\s*[-*]\s+)\[\s\](\s+{re.escape(item)}\s*)$")


def render_checklist(source: str, payload: DonePayload) -> str:
    result = source
    for item in payload.completed_items:
        pattern = _unchecked_pattern(item)
        matches = list(pattern.finditer(result))
        if len(matches) != 1:
            raise MbError(f"expected exactly one unchecked item for completion: {item}", 1)
        result = pattern.sub(r"\1[x]\2", result, count=1)
    existing = set(re.findall(r"(?m)^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$", result))
    additions = [item for item in payload.new_items if item not in existing]
    if additions:
        block = f"{ITEMS_START}\n## Memory Bank Lite Open Items\n\n" + "\n".join(f"- [ ] {item}" for item in additions) + f"\n{ITEMS_END}"
        current = re.search(re.escape(ITEMS_START) + r"(.*?)" + re.escape(ITEMS_END), result, re.DOTALL)
        if current:
            old_body = current.group(1).rstrip()
            block = f"{ITEMS_START}{old_body}\n" + "\n".join(f"- [ ] {item}" for item in additions) + f"\n{ITEMS_END}"
        result = _replace_or_insert(result, ITEMS_START, ITEMS_END, block, False)
    return result


def _bullet_section(title: str, values: tuple[str, ...], code: bool = False) -> str:
    if not values:
        return f"## {title}\n- None"
    bullets = [f"- `{value}`" if code else f"- {value}" for value in values]
    return f"## {title}\n" + "\n".join(bullets)


def render_progress_entry(payload: DonePayload, timestamp: str) -> str:
    compact = timestamp.replace("-", "").replace(":", "")
    compact = compact.replace("T", "T").replace("Z", "Z")
    marker_hash = sha256((timestamp + "\0" + payload.summary).encode("utf-8")).hexdigest()[:8]
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
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_done_transform -v`

Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 5: Commit compatible transforms**

```bash
git add scripts/mb_lite/done.py tests/test_done_transform.py
git commit -m "feat: transform compatible memory markdown"
```

### Task 3: Locked Apply, Notes, And Conflict Refusal

**Files:**
- Modify: `scripts/mb_lite/done.py`
- Create: `tests/test_done_apply.py`

- [ ] **Step 1: Write failing apply and conflict tests**

```python
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from scripts.mb_lite.done import DonePayload, apply_done, bank_revision
from scripts.mb_lite.errors import MbError


def payload(bank: Path, note: dict[str, str] | None = None) -> DonePayload:
    return DonePayload.from_json(json.dumps({
        "bank_revision": bank_revision(bank),
        "summary": "Implemented registry",
        "decisions": ["Use central storage"],
        "next_steps": ["Implement start"],
        "blockers": [],
        "key_files": ["scripts/mb_lite/registry.py"],
        "completed_items": ["Define registry schema"],
        "new_items": ["Implement start"],
        "note": note,
    }))


class DoneApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.bank = self.root / "bank"
        self.repo.mkdir()
        self.bank.mkdir()
        (self.repo / "scripts" / "mb_lite").mkdir(parents=True)
        (self.repo / "scripts" / "mb_lite" / "registry.py").write_text("registry = True\n")
        (self.bank / "status.md").write_text("# Status\nlegacy\n")
        (self.bank / "checklist.md").write_text("# Checklist\n- [ ] Define registry schema\n")
        (self.bank / "progress.md").write_text("# Progress\nlegacy\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_apply_preserves_progress_prefix_and_creates_note_only_when_requested(self) -> None:
        original = (self.bank / "progress.md").read_bytes()
        result = apply_done(
            self.bank,
            self.repo,
            payload(self.bank, {"title": "Central registry", "body": "Use one registry for all hosts."}),
            datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        )
        self.assertTrue((self.bank / "progress.md").read_bytes().startswith(original))
        self.assertEqual(result.note_path, "notes/2026-07-10_central-registry.md")
        self.assertTrue((self.bank / result.note_path).is_file())

    def test_revision_conflict_writes_nothing(self) -> None:
        draft = payload(self.bank)
        (self.bank / "status.md").write_text("# Status\nchanged elsewhere\n")
        before = {path.name: path.read_bytes() for path in self.bank.iterdir() if path.is_file()}
        with self.assertRaisesRegex(MbError, "Bank revision changed") as raised:
            apply_done(self.bank, self.repo, draft, datetime.now(timezone.utc))
        self.assertEqual(raised.exception.exit_code, 3)
        after = {path.name: path.read_bytes() for path in self.bank.iterdir() if path.is_file()}
        self.assertEqual(before, after)

    def test_key_file_outside_repository_is_rejected(self) -> None:
        raw = payload(self.bank)
        invalid = DonePayload(
            raw.bank_revision, raw.summary, raw.decisions, raw.next_steps,
            raw.blockers, ("../secret",), raw.completed_items, raw.new_items, raw.note,
        )
        with self.assertRaisesRegex(MbError, "key file is outside repository"):
            apply_done(self.bank, self.repo, invalid, datetime.now(timezone.utc))

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
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_done_apply -v`

Expected: import errors for `apply_done`.

- [ ] **Step 3: Implement locked, validated, atomic apply**

Append to `scripts/mb_lite/done.py`:

```python
from contextlib import contextmanager
import fcntl
import os
import tempfile
from typing import Iterator


@dataclass(frozen=True)
class ApplyResult:
    revision: str
    note_path: str | None


@contextmanager
def _bank_lock(bank: Path) -> Iterator[None]:
    lock = bank / ".mb-lite.lock"
    with lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise MbError(f"Bank lock acquisition failed: {exc}", 5) from exc
        yield


def _atomic_write(path: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
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
        checklist = render_checklist(checklist_path.read_text(encoding="utf-8"), payload)
        progress_source = progress_path.read_text(encoding="utf-8")
        separator = "" if progress_source.endswith("\n") else "\n"
        progress = progress_source + separator + "\n" + render_progress_entry(payload, timestamp)
        note_relative: str | None = None
        note_target: Path | None = None
        note_content: str | None = None
        if payload.note:
            note_relative = f"notes/{now:%Y-%m-%d}_{_slug(payload.note.title)}.md"
            note_target = bank / note_relative
            if note_target.exists():
                raise MbError(f"note already exists: {note_relative}", 1)
            note_target.parent.mkdir(parents=True, exist_ok=True)
            note_content = f"# {payload.note.title}\n\n{payload.note.body.rstrip()}\n"
        _atomic_write(status_path, status)
        _atomic_write(checklist_path, checklist)
        _atomic_write(progress_path, progress)
        if note_target and note_content is not None:
            _atomic_write(note_target, note_content)
        return ApplyResult(bank_revision(bank), note_relative)
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_done_apply -v`

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit confirmed apply**

```bash
git add scripts/mb_lite/done.py tests/test_done_apply.py
git commit -m "feat: apply confirmed memory updates"
```

### Task 4: CLI And Skill Two-Phase Workflow

**Files:**
- Modify: `scripts/mb.py`
- Modify: `SKILL.md`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing CLI confirmation-boundary tests**

Add this method to `CliTest` in `tests/test_cli.py`:

```python
def test_done_revision_is_read_only_and_apply_requires_payload(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / "repo"
        home = root / "memory"
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
        env = {**os.environ, "MB_LITE_HOME": str(home)}
        subprocess.run(["python3", str(CLI), "--project-root", str(repo), "init"], env=env, check=True, capture_output=True)
        revision = subprocess.run(
            ["python3", str(CLI), "--project-root", str(repo), "done", "--revision", "--json"],
            env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(revision.returncode, 0, revision.stderr)
        bank = next((home / "projects").glob("*/.memory-bank"))
        before = {path.name: path.read_bytes() for path in bank.iterdir() if path.is_file()}
        self.assertEqual(len(json.loads(revision.stdout)["bank_revision"]), 64)
        after = {path.name: path.read_bytes() for path in bank.iterdir() if path.is_file()}
        self.assertEqual(before, after)
        rejected = subprocess.run(
            ["python3", str(CLI), "--project-root", str(repo), "done", "--apply"],
            env=env, input="", capture_output=True, text=True, check=False,
        )
        self.assertEqual(rejected.returncode, 1)
```

Add `import json` to the test module imports.

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest tests.test_cli.CliTest.test_done_revision_is_read_only_and_apply_requires_payload -v`

Expected: argparse rejects the unknown `done` action.

- [ ] **Step 3: Add `done` to the CLI**

Add imports to `scripts/mb.py`:

```python
from datetime import datetime, timezone
from mb_lite.done import DonePayload, apply_done, bank_revision
```

Add to `_parser()`:

```python
done = sub.add_parser("done")
mode = done.add_mutually_exclusive_group(required=True)
mode.add_argument("--revision", action="store_true")
mode.add_argument("--apply", action="store_true")
done.add_argument("--json", action="store_true")
```

Add before `start` handling in `main()`:

```python
if args.action == "done":
    if args.revision:
        payload = {"repository_key": identity.key, "bank_path": str(bank), "bank_revision": bank_revision(bank)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["bank_revision"])
        return 0
    raw = sys.stdin.read()
    if not raw.strip():
        raise MbError("done --apply requires a confirmed JSON payload on stdin", 1)
    result = apply_done(bank, identity.root, DonePayload.from_json(raw), datetime.now(timezone.utc))
    payload = {"bank_revision": result.revision, "note_path": result.note_path}
    print(json.dumps(payload, ensure_ascii=False) if args.json else "Memory saved.")
    return 0
```

- [ ] **Step 4: Replace the `done` paragraph in `SKILL.md` with the exact protocol**

```markdown
## done

1. Run `python3 <skill-dir>/scripts/mb.py --project-root "$PWD" done --revision --json`.
2. Inspect the current conversation and `git status --short` plus `git diff --stat`.
3. Draft JSON with the returned `bank_revision` and fields `summary`, `decisions`, `next_steps`, `blockers`, `key_files`, `completed_items`, `new_items`, and `note`.
4. Show a readable draft to the user. Do not write any file.
5. Ask for explicit confirmation.
6. Only after confirmation, pipe the exact JSON to `done --apply --json`.
7. Report the resulting revision and note path.

If confirmation is absent, ambiguous, or the session ends, save nothing.
If apply returns exit code 3, regenerate the draft from the new Bank revision.
```

- [ ] **Step 5: Run the CLI and full test suite**

Run:

```bash
python3 -m unittest tests.test_cli -v
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

Expected: all tests pass and compile exits `0`.

- [ ] **Step 6: Commit the explicit done workflow**

```bash
git add SKILL.md scripts/mb.py tests/test_cli.py
git commit -m "feat: expose confirmed done workflow"
```

### Task 5: Concurrent Apply Contract

**Files:**
- Modify: `tests/test_done_apply.py`

- [ ] **Step 1: Add a failing two-process apply test**

Add this import beside the existing standard-library imports at module scope:

```python
from multiprocessing import get_context
```

Add this worker immediately after the existing module-level `payload()` helper
and before `class DoneApplyTest`:

```python

def _apply_worker(bank: str, repo: str, raw: str, queue) -> None:
    try:
        value = DonePayload.from_json(raw)
        apply_done(Path(bank), Path(repo), value, datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc))
        queue.put(0)
    except MbError as exc:
        queue.put(exc.exit_code)
```

Add this method at four-space indentation inside `DoneApplyTest`, after
`test_key_file_outside_repository_is_rejected`:

```python
    def test_two_concurrent_applies_allow_one_writer(self) -> None:
        draft = payload(self.bank)
        raw = json.dumps({
            "bank_revision": draft.bank_revision,
            "summary": draft.summary,
            "decisions": list(draft.decisions),
            "next_steps": list(draft.next_steps),
            "blockers": list(draft.blockers),
            "key_files": list(draft.key_files),
            "completed_items": list(draft.completed_items),
            "new_items": list(draft.new_items),
            "note": None,
        })
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
        self.assertEqual(sorted(queue.get(timeout=2) for _ in range(2)), [0, 3])
```

- [ ] **Step 2: Run the concurrency test**

Run: `python3 -m unittest tests.test_done_apply.DoneApplyTest.test_two_concurrent_applies_allow_one_writer -v`

Expected: PASS with exactly one worker returning `0` and one returning exit code
`3`. A two-success result is a hard failure and means the Task 3 implementation
does not match its specified critical section.

- [ ] **Step 3: Verify revision comparison remains inside `_bank_lock`**

Inspect `apply_done()` and verify that no Bank-state read has moved ahead of the
lock. The implemented critical section must still be exactly:

```python
with _bank_lock(bank):
    current_revision = bank_revision(bank)
    if current_revision != payload.bank_revision:
        raise MbError("Bank revision changed; regenerate the done draft", 3)
    status = render_status((bank / "status.md").read_text(encoding="utf-8"), payload)
    checklist = render_checklist((bank / "checklist.md").read_text(encoding="utf-8"), payload)
```

- [ ] **Step 4: Run the full suite and verify GREEN**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass, including exactly one success and one exit-code-3 conflict.

- [ ] **Step 5: Commit concurrency coverage**

```bash
git add tests/test_done_apply.py
git commit -m "test: enforce single-writer done apply"
```

## Milestone Verification

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
rg -n "SessionStart|SessionEnd|PreCompact|subagent|hooks" SKILL.md scripts
git status --short
```

Expected:

- every test passes;
- compile exits `0`;
- the `rg` command returns no automatic lifecycle implementation;
- an unconfirmed draft is never passed to `done --apply`;
- a stale revision returns exit code `3` without modifying Bank files;
- the working tree is clean.
