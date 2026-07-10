# Memory Bank Lite Core Read Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standard-library CLI that identifies a Git repository, registers its compatible global Bank, and provides bounded read-only `start` and `recall` actions.

**Architecture:** `scripts/mb.py` is the only executable entry point and delegates to focused modules under `scripts/mb_lite/`. Repository identity is derived from a normalized Git remote, the central registry is the only source of Bank paths, and read commands never write or repair data.

**Tech Stack:** Python 3.11+ standard library, `unittest`, Bash 3.2 for smoke commands, JSON, Markdown.

---

## File Structure

- Create `scripts/mb.py`: argument parsing and exit-code translation only.
- Create `scripts/mb_lite/errors.py`: typed CLI errors and stable exit codes.
- Create `scripts/mb_lite/identity.py`: Git root/remote selection, normalization, and project ID derivation.
- Create `scripts/mb_lite/registry.py`: central registry schema, locking, containment, and atomic writes.
- Create `scripts/mb_lite/bank.py`: compatible Bank initialization and active-state validation.
- Create `scripts/mb_lite/start.py`: bounded context extraction and Markdown/JSON rendering.
- Create `scripts/mb_lite/recall.py`: deterministic lexical search and expansion.
- Create `templates/.memory-bank/{status,checklist,progress,roadmap,research,backlog,lessons}.md`: compatible initial files.
- Create `tests/test_identity.py`: repository identity contracts.
- Create `tests/test_registry.py`: registry safety and concurrency contracts.
- Create `tests/test_bank.py`: initialization and active-state contracts.
- Create `tests/test_start.py`: selection, cap, and read-only contracts.
- Create `tests/test_recall.py`: search, IDs, expansion, and read-only contracts.
- Create `tests/test_cli.py`: end-to-end CLI and exit-code contracts.
- Create `SKILL.md`: minimal explicit `init`, `start`, and `recall` workflow for this milestone.

### Task 1: Repository Identity

**Files:**
- Create: `scripts/mb_lite/__init__.py`
- Create: `scripts/mb_lite/errors.py`
- Create: `scripts/mb_lite/identity.py`
- Create: `tests/__init__.py`
- Create: `tests/test_identity.py`

- [ ] **Step 1: Write the failing normalization and selection tests**

```python
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.identity import normalize_remote, resolve_identity


class IdentityTest(unittest.TestCase):
    def test_equivalent_remote_forms_have_one_key(self) -> None:
        expected = "github.com/example/onduty"
        self.assertEqual(normalize_remote("git@github.com:example/onduty.git"), expected)
        self.assertEqual(normalize_remote("ssh://git@github.com/example/onduty.git"), expected)
        self.assertEqual(normalize_remote("https://token@github.com/example/onduty.git?x=1#f"), expected)

    @patch("scripts.mb_lite.identity._git")
    def test_push_default_wins_over_origin(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "upstream",
            ("remote",): "origin\nupstream",
            ("remote", "get-url", "upstream"): "git@github.com:team/project.git",
        }[args]
        identity = resolve_identity(Path("/tmp/repo"))
        self.assertEqual(identity.key, "github.com/team/project")
        self.assertEqual(identity.project_id, "project-" + identity.digest)

    @patch("scripts.mb_lite.identity._git")
    def test_origin_wins_when_push_default_is_absent(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "origin\nupstream",
            ("remote", "get-url", "origin"): "https://github.com/team/project.git",
        }[args]
        self.assertEqual(resolve_identity(Path("/tmp/repo")).key, "github.com/team/project")

    @patch("scripts.mb_lite.identity._git")
    def test_only_remote_is_selected_without_origin(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "upstream",
            ("remote", "get-url", "upstream"): "git@github.com:team/project.git",
        }[args]
        self.assertEqual(resolve_identity(Path("/tmp/repo")).key, "github.com/team/project")

    @patch("scripts.mb_lite.identity._git")
    def test_clones_with_same_remote_share_project_id(self, git) -> None:
        def values(root, *args):
            if args == ("rev-parse", "--show-toplevel"):
                return str(root)
            return {
                ("config", "--get", "remote.pushDefault"): "",
                ("remote",): "origin",
                ("remote", "get-url", "origin"): "git@github.com:team/project.git",
            }[args]

        git.side_effect = values
        first = resolve_identity(Path("/tmp/clone-one"))
        second = resolve_identity(Path("/tmp/clone-two"))
        self.assertEqual(first.project_id, second.project_id)

    @patch("scripts.mb_lite.identity._git")
    def test_multiple_remotes_without_origin_are_rejected(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "one\ntwo",
        }[args]
        with self.assertRaisesRegex(MbError, "multiple Git remotes") as raised:
            resolve_identity(Path("/tmp/repo"))
        self.assertEqual(raised.exception.exit_code, 1)

    @patch("scripts.mb_lite.identity._git")
    def test_no_remote_falls_back_to_canonical_root(self, git) -> None:
        git.side_effect = lambda root, *args: {
            ("rev-parse", "--show-toplevel"): "/tmp/repo",
            ("config", "--get", "remote.pushDefault"): "",
            ("remote",): "",
        }[args]
        identity = resolve_identity(Path("/tmp/repo"))
        self.assertEqual(identity.key, "local:/tmp/repo")
        self.assertIsNone(identity.remote)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_identity -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite'`.

- [ ] **Step 3: Implement typed errors and repository identity**

Create `scripts/mb_lite/errors.py`:

```python
class MbError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code
```

Create `scripts/mb_lite/identity.py`:

```python
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .errors import MbError


@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    key: str
    slug: str
    digest: str
    project_id: str
    remote: str | None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if args == ("rev-parse", "--show-toplevel"):
            raise MbError(f"not a Git repository: {root}", 1)
        return ""
    return result.stdout.strip()


def normalize_remote(value: str) -> str:
    raw = value.strip()
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
    if scp and "://" not in raw:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urlsplit(raw)
        host, path = parsed.hostname or "", parsed.path
    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not path:
        raise MbError(f"unsupported Git remote URL: {value}", 1)
    return f"{host}/{path}"


def _slug(key: str) -> str:
    tail = key.rsplit("/", 1)[-1]
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.").lower()
    return value or "project"


def resolve_identity(project_root: Path) -> RepositoryIdentity:
    root = Path(_git(project_root, "rev-parse", "--show-toplevel")).resolve()
    push_default = _git(root, "config", "--get", "remote.pushDefault")
    remotes = [line for line in _git(root, "remote").splitlines() if line]
    if push_default:
        selected = push_default
    elif "origin" in remotes:
        selected = "origin"
    elif len(remotes) == 1:
        selected = remotes[0]
    elif len(remotes) > 1:
        raise MbError("multiple Git remotes; configure remote.pushDefault or origin", 1)
    else:
        selected = ""
    remote = _git(root, "remote", "get-url", selected) if selected else None
    key = normalize_remote(remote) if remote else f"local:{root}"
    digest = sha256(key.encode("utf-8")).hexdigest()[:12]
    slug = _slug(key)
    return RepositoryIdentity(root, key, slug, digest, f"{slug}-{digest}", remote)
```

Create empty `scripts/mb_lite/__init__.py` and `tests/__init__.py`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_identity -v`

Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Commit repository identity**

```bash
git add scripts/mb_lite tests/test_identity.py tests/__init__.py
git commit -m "feat: resolve repository identity"
```

### Task 2: Central Registry

**Files:**
- Create: `scripts/mb_lite/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write failing registry safety tests**

```python
from pathlib import Path
import json
from multiprocessing import get_context
import tempfile
import unittest

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.identity import RepositoryIdentity
from scripts.mb_lite.registry import RegistryStore


def _register_worker(home: str, root: str, queue) -> None:
    identity = RepositoryIdentity(
        Path(root),
        "github.com/example/onduty",
        "onduty",
        "122d9ee9bd04",
        "onduty-122d9ee9bd04",
        "git@github.com:example/onduty.git",
    )
    try:
        RegistryStore(Path(home)).register(identity)
        queue.put(0)
    except MbError as exc:
        queue.put(exc.exit_code)


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.identity = RepositoryIdentity(
            Path("/tmp/onduty"),
            "github.com/example/onduty",
            "onduty",
            "122d9ee9bd04",
            "onduty-122d9ee9bd04",
            "git@github.com:example/onduty.git",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_register_is_idempotent_and_tracks_known_root(self) -> None:
        store = RegistryStore(self.home)
        first = store.register(self.identity)
        second = store.register(self.identity)
        self.assertEqual(first, second)
        data = json.loads((self.home / "registry.json").read_text())
        entry = data["projects"][self.identity.key]
        self.assertEqual(entry["known_roots"], ["/tmp/onduty"])

    def test_corrupt_registry_is_never_overwritten(self) -> None:
        path = self.home / "registry.json"
        path.write_text("{broken", encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaisesRegex(MbError, "corrupt registry"):
            RegistryStore(self.home).register(self.identity)
        self.assertEqual(path.read_bytes(), before)

    def test_lookup_rejects_bank_outside_projects_root(self) -> None:
        store = RegistryStore(self.home)
        store.register(self.identity)
        data = json.loads(store.path.read_text())
        data["projects"][self.identity.key]["bank_path"] = "/tmp/escape/.memory-bank"
        store.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(MbError, "outside projects_root"):
            store.lookup(self.identity.key)

    def test_adopt_reuses_old_bank_and_preserves_old_key(self) -> None:
        store = RegistryStore(self.home)
        old = store.register(self.identity)
        changed = RepositoryIdentity(
            Path("/tmp/onduty-renamed"),
            "github.com/example/onduty-renamed",
            "onduty-renamed",
            "abcdef123456",
            "onduty-renamed-abcdef123456",
            "git@github.com:example/onduty-renamed.git",
        )
        adopted = store.adopt(changed, self.identity.key)
        self.assertEqual(adopted.bank_path, old.bank_path)
        self.assertEqual(store.lookup(self.identity.key).bank_path, old.bank_path)
        self.assertEqual(adopted.known_roots, ("/tmp/onduty", "/tmp/onduty-renamed"))

    def test_concurrent_registration_preserves_both_known_roots(self) -> None:
        context = get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_register_worker,
                args=(str(self.home), f"/tmp/clone-{number}", queue),
            )
            for number in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([queue.get(timeout=2) for _ in range(2)], [0, 0])
        entry = RegistryStore(self.home).lookup(self.identity.key)
        self.assertEqual(entry.known_roots, ("/tmp/clone-0", "/tmp/clone-1"))
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_registry -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.registry'`.

- [ ] **Step 3: Implement locked atomic registry access**

Create `scripts/mb_lite/registry.py`:

```python
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator

from .errors import MbError
from .identity import RepositoryIdentity


@dataclass(frozen=True)
class RegistryEntry:
    project_id: str
    remote: str | None
    bank_path: Path
    known_roots: tuple[str, ...]


class RegistryStore:
    def __init__(self, home: Path) -> None:
        self.home = home.resolve()
        self.projects_root = self.home / "projects"
        self.path = self.home / "registry.json"
        self.lock_path = self.home / ".registry.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.home.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise MbError(f"registry lock acquisition failed: {exc}", 5) from exc
            yield

    def _empty(self) -> dict[str, object]:
        return {"version": 1, "projects_root": str(self.projects_root), "projects": {}}

    def _load(self, allow_missing: bool) -> dict[str, object]:
        if not self.path.exists():
            if allow_missing:
                return self._empty()
            raise MbError(f"central registry not found: {self.path}", 2)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MbError(f"corrupt registry: {self.path}", 4) from exc
        if data.get("version") != 1 or not isinstance(data.get("projects"), dict):
            raise MbError(f"corrupt registry schema: {self.path}", 4)
        return data

    def _write(self, data: dict[str, object]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".registry.", dir=self.home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def bank_path(self, identity: RepositoryIdentity) -> Path:
        return self.projects_root / identity.project_id / ".memory-bank"

    def register(self, identity: RepositoryIdentity) -> RegistryEntry:
        with self.locked():
            data = self._load(allow_missing=True)
            projects = data["projects"]
            assert isinstance(projects, dict)
            bank = self.bank_path(identity)
            current = projects.get(identity.key, {})
            roots = set(current.get("known_roots", [])) if isinstance(current, dict) else set()
            roots.add(str(identity.root))
            projects[identity.key] = {
                "project_id": identity.project_id,
                "remote": identity.key if identity.remote else None,
                "bank_path": str(bank),
                "known_roots": sorted(roots),
            }
            self._write(data)
        return self.lookup(identity.key)

    def lookup(self, key: str) -> RegistryEntry:
        data = self._load(allow_missing=False)
        projects = data["projects"]
        assert isinstance(projects, dict)
        raw = projects.get(key)
        if not isinstance(raw, dict):
            raise MbError(f"Memory Bank is not initialized for repository {key}", 2)
        bank = Path(str(raw.get("bank_path", ""))).resolve()
        try:
            bank.relative_to(self.projects_root.resolve())
        except ValueError as exc:
            raise MbError(f"bank_path is outside projects_root: {bank}", 4) from exc
        roots = tuple(str(value) for value in raw.get("known_roots", []))
        remote = raw.get("remote")
        return RegistryEntry(str(raw["project_id"]), str(remote) if remote else None, bank, roots)

    def adopt(self, identity: RepositoryIdentity, old_key: str) -> RegistryEntry:
        with self.locked():
            data = self._load(allow_missing=False)
            projects = data["projects"]
            assert isinstance(projects, dict)
            old = projects.get(old_key)
            if not isinstance(old, dict):
                raise MbError(f"cannot adopt unknown repository key: {old_key}", 2)
            bank = Path(str(old.get("bank_path", ""))).resolve()
            try:
                bank.relative_to(self.projects_root.resolve())
            except ValueError as exc:
                raise MbError(f"bank_path is outside projects_root: {bank}", 4) from exc
            roots = {str(value) for value in old.get("known_roots", [])}
            roots.add(str(identity.root))
            projects[identity.key] = {
                "project_id": str(old["project_id"]),
                "remote": identity.key if identity.remote else None,
                "bank_path": str(bank),
                "known_roots": sorted(roots),
            }
            self._write(data)
        return self.lookup(identity.key)
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_registry -v`

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Commit central registry**

```bash
git add scripts/mb_lite/registry.py tests/test_registry.py
git commit -m "feat: add central memory registry"
```

### Task 3: Compatible Bank Initialization And Validation

**Files:**
- Create: `scripts/mb_lite/bank.py`
- Create: `templates/.memory-bank/status.md`
- Create: `templates/.memory-bank/checklist.md`
- Create: `templates/.memory-bank/progress.md`
- Create: `templates/.memory-bank/roadmap.md`
- Create: `templates/.memory-bank/research.md`
- Create: `templates/.memory-bank/backlog.md`
- Create: `templates/.memory-bank/lessons.md`
- Create: `tests/test_bank.py`

- [ ] **Step 1: Write failing initialization and validation tests**

```python
from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.bank import initialize_bank, validate_bank
from scripts.mb_lite.errors import MbError


class BankTest(unittest.TestCase):
    def test_initialize_creates_compatible_files_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory) / ".memory-bank"
            initialize_bank(bank)
            original = "# User Status\n"
            (bank / "status.md").write_text(original, encoding="utf-8")
            initialize_bank(bank)
            self.assertEqual((bank / "status.md").read_text(), original)
            self.assertTrue((bank / "notes").is_dir())
            self.assertTrue((bank / "plans" / "done").is_dir())

    def test_validate_requires_three_readable_core_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory) / ".memory-bank"
            bank.mkdir()
            (bank / "status.md").write_text("# Status\n")
            (bank / "checklist.md").write_text("# Checklist\n")
            with self.assertRaisesRegex(MbError, "missing readable progress.md"):
                validate_bank(bank)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_bank -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.bank'`.

- [ ] **Step 3: Add compatible templates and initialization**

Create the required template bodies exactly:

```markdown
# Project Status

<!-- mb-lite:current:start -->
## Memory Bank Lite Current

**Focus:** Not set
**Next:** Run `done` after completing meaningful work
**Blockers:** None
<!-- mb-lite:current:end -->
```

```markdown
# Checklist

<!-- mb-lite:open-items:start -->
## Memory Bank Lite Open Items

<!-- mb-lite:open-items:end -->
```

```markdown
# Progress
```

Use one-line H1 templates for `roadmap.md`, `research.md`, `backlog.md`, and
`lessons.md` with titles `# Roadmap`, `# Research`, `# Backlog`, and `# Lessons`.

Create `scripts/mb_lite/bank.py`:

```python
from pathlib import Path

from .errors import MbError


CORE_FILES = ("status.md", "checklist.md", "progress.md")
OPTIONAL_FILES = ("roadmap.md", "research.md", "backlog.md", "lessons.md")


def template_root() -> Path:
    return Path(__file__).resolve().parents[2] / "templates" / ".memory-bank"


def initialize_bank(bank: Path) -> None:
    bank.mkdir(parents=True, exist_ok=True)
    for directory in (bank / "notes", bank / "plans" / "done"):
        directory.mkdir(parents=True, exist_ok=True)
    source = template_root()
    for name in (*CORE_FILES, *OPTIONAL_FILES):
        target = bank / name
        if not target.exists():
            target.write_bytes((source / name).read_bytes())


def validate_bank(bank: Path) -> Path:
    if not bank.is_dir():
        raise MbError(f"Bank directory does not exist: {bank}", 4)
    for name in CORE_FILES:
        path = bank / name
        try:
            with path.open("r", encoding="utf-8"):
                pass
        except OSError as exc:
            raise MbError(f"missing readable {name}: {path}", 4) from exc
    return bank
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_bank -v`

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit compatible Bank initialization**

```bash
git add scripts/mb_lite/bank.py templates tests/test_bank.py
git commit -m "feat: initialize compatible memory banks"
```

### Task 4: Bounded Read-Only Start

**Files:**
- Create: `scripts/mb_lite/start.py`
- Create: `tests/test_start.py`

- [ ] **Step 1: Write failing context selection and byte-cap tests**

```python
from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.start import build_start_context


class StartTest(unittest.TestCase):
    def test_start_returns_open_items_and_three_recent_entries_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "status.md").write_text(
                "# Status\n<!-- mb-lite:current:start -->\n## Memory Bank Lite Current\n"
                "**Focus:** Registry\n**Next:** Add recall\n**Blockers:** None\n"
                "<!-- mb-lite:current:end -->\n",
                encoding="utf-8",
            )
            (bank / "checklist.md").write_text("# Checklist\n- [ ] open\n- [x] closed\n", encoding="utf-8")
            entries = "\n".join(
                f"<!-- mb-lite-entry:id-{number} -->\n### 2026-07-0{number} 12:00 - item {number}\nbody {number}"
                for number in range(1, 5)
            )
            (bank / "progress.md").write_text("# Progress\n" + entries, encoding="utf-8")
            before = {path.name: path.read_bytes() for path in bank.iterdir()}
            output = build_start_context(bank)
            after = {path.name: path.read_bytes() for path in bank.iterdir()}
            self.assertIn("- [ ] open", output)
            self.assertNotIn("closed", output)
            self.assertNotIn("item 1", output)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
            self.assertEqual(before, after)

    def test_protected_sections_over_cap_fail_instead_of_truncating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "status.md").write_text("# Status\n**Focus:** X\n**Next:** Y\n**Blockers:** None\n")
            (bank / "checklist.md").write_text("- [ ] " + "z" * 9000)
            (bank / "progress.md").write_text("# Progress\n")
            with self.assertRaisesRegex(MbError, r"current=\d+, open-items=\d+"):
                build_start_context(bank)

    def test_start_selects_first_open_stage_and_at_most_five_matching_note_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "status.md").write_text(
                "# Status\n**Focus:** Central Registry\n**Next:** Add recall\n**Blockers:** None\n",
                encoding="utf-8",
            )
            (bank / "checklist.md").write_text("# Checklist\n- [ ] ship\n", encoding="utf-8")
            (bank / "progress.md").write_text("# Progress\n", encoding="utf-8")
            (bank / "plans").mkdir()
            (bank / "plans/2026-registry.md").write_text(
                "# Registry Plan\n\n### Stage 1: Finished\n- [x] done\n\n"
                "### Stage 2: Registry parser\n- [ ] parse central registry\nSupporting detail.\n",
                encoding="utf-8",
            )
            (bank / "notes").mkdir()
            for number in range(1, 7):
                (bank / f"notes/{number}.md").write_text(
                    f"# Registry Note {number}\nbody\n",
                    encoding="utf-8",
                )
            before = {path: path.read_bytes() for path in bank.rglob("*") if path.is_file()}
            output = build_start_context(bank)
            after = {path: path.read_bytes() for path in bank.rglob("*") if path.is_file()}
            self.assertIn("Stage 2: Registry parser", output)
            self.assertNotIn("Stage 1: Finished", output)
            self.assertEqual(output.count("- Registry Note"), 5)
            self.assertNotIn("Registry Note 6", output)
            self.assertEqual(before, after)

    def test_start_trims_notes_then_progress_then_stage_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "status.md").write_text("# Status\n**Focus:** Registry\n**Next:** Ship\n**Blockers:** None\n")
            (bank / "checklist.md").write_text("# Checklist\n- [ ] ship\n")
            (bank / "progress.md").write_text(
                "# Progress\n<!-- mb-lite-entry:id-1 -->\n### Old entry\n" + "p" * 1000,
                encoding="utf-8",
            )
            (bank / "plans").mkdir()
            (bank / "plans/active.md").write_text(
                "# Plan\n### Stage 1: Registry stage\n- [ ] ship\n" + "s" * 9000,
                encoding="utf-8",
            )
            (bank / "notes").mkdir()
            (bank / "notes/registry.md").write_text("# Registry Note\n", encoding="utf-8")
            output = build_start_context(bank)
            self.assertNotIn("Registry Note", output)
            self.assertNotIn("Old entry", output)
            self.assertIn("Stage 1: Registry stage", output)
            self.assertNotIn("s" * 200, output)
            self.assertIn("optional context was trimmed", output)
            self.assertLessEqual(len(output.encode("utf-8")), 8192)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_start -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.start'`.

- [ ] **Step 3: Implement deterministic start extraction**

Create `scripts/mb_lite/start.py` with these complete public functions and
helpers. The lexicographically newest top-level plan containing an unchecked
stage is active; its first unchecked `### Stage N:` block is current. This is
deterministic and ignores historical files under `plans/done/`:

```python
from pathlib import Path
import re

from .errors import MbError


MAX_BYTES = 8192
CURRENT_RE = re.compile(
    r"<!-- mb-lite:current:start -->(.*?)<!-- mb-lite:current:end -->",
    re.DOTALL,
)
ENTRY_RE = re.compile(
    r"(?=<!-- mb-lite-entry:)(<!-- mb-lite-entry:[^>]+ -->.*?)(?=<!-- mb-lite-entry:|\Z)",
    re.DOTALL,
)
STAGE_RE = re.compile(
    r"(?ms)^###\s+(Stage\s+\d+:\s*.+?)\s*$\n(.*?)(?=^###\s+Stage\s+\d+:|\Z)"
)


def _current(status: str) -> str:
    match = CURRENT_RE.search(status)
    if match:
        return match.group(1).strip()
    lines = [line for line in status.splitlines() if line.startswith(("**Focus:**", "**Next:**", "**Blockers:**"))]
    return "\n".join(lines) or "Current state is not recorded."


def _open_items(checklist: str) -> str:
    items = [line for line in checklist.splitlines() if re.match(r"^\s*[-*]\s+\[\s\]\s+", line)]
    return "\n".join(items) or "No open items."


def _focus(current: str) -> str:
    match = re.search(r"(?m)^\*\*Focus:\*\*\s*(.+)$", current)
    return match.group(1).strip() if match else ""


def _active_stage(bank: Path) -> tuple[str, str]:
    plans = bank / "plans"
    if not plans.is_dir():
        return "", ""
    for path in sorted(plans.glob("*.md"), reverse=True):
        source = path.read_text(encoding="utf-8")
        for match in STAGE_RE.finditer(source):
            if re.search(r"(?m)^\s*[-*]\s+\[\s\]\s+", match.group(2)):
                return match.group(1).strip(), match.group(2).strip()
    return "", ""


def _terms(value: str) -> tuple[str, ...]:
    return tuple(term for term in re.split(r"[\W_]+", value.casefold()) if len(term) >= 2)


def _note_titles(bank: Path, focus: str, stage_title: str) -> list[str]:
    terms = _terms(f"{focus} {stage_title}")
    notes = bank / "notes"
    if not terms or not notes.is_dir():
        return []
    result: list[str] = []
    for path in sorted(notes.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        heading = re.search(r"(?m)^#\s+(.+)$", source)
        title = heading.group(1).strip() if heading else path.stem
        folded = title.casefold()
        if any(term in folded for term in terms):
            result.append(title)
        if len(result) == 5:
            break
    return result


def _recent(progress: str) -> list[str]:
    lite = [match.group(1).strip() for match in ENTRY_RE.finditer(progress)]
    if len(lite) >= 3:
        return lite[-3:]
    legacy_source = ENTRY_RE.sub("", progress)
    legacy = [entry.strip() for entry in re.findall(r"(?ms)^###\s+.+?(?=^###\s+|\Z)", legacy_source)]
    needed = 3 - len(lite)
    return legacy[-needed:] + lite


def _render(
    current: str,
    open_items: str,
    stage_title: str,
    stage_detail: str,
    recent: list[str],
    note_titles: list[str],
    trimmed: bool,
) -> str:
    sections = ["# Memory Bank", "## Current", current, "## Open Items", open_items]
    if stage_title:
        stage = f"### {stage_title}"
        if stage_detail:
            stage += "\n" + stage_detail
        sections.extend(("## Active Stage", stage))
    if recent:
        sections.extend(("## Recent Progress", "\n\n".join(recent)))
    if note_titles:
        sections.extend(("## Matching Notes", "\n".join(f"- {title}" for title in note_titles)))
    if trimmed:
        sections.extend(("## Notice", "Older optional context was trimmed to stay within 8192 bytes."))
    return "\n\n".join(sections).rstrip() + "\n"


def build_start_context(bank: Path, section: str | None = None) -> str:
    status = (bank / "status.md").read_text(encoding="utf-8")
    checklist = (bank / "checklist.md").read_text(encoding="utf-8")
    progress = (bank / "progress.md").read_text(encoding="utf-8")
    current = _current(status)
    open_items = _open_items(checklist)
    stage_title, stage_detail = _active_stage(bank)
    recent = _recent(progress)
    note_titles = _note_titles(bank, _focus(current), stage_title)
    sections = {
        "current": current,
        "open-items": open_items,
        "active-stage": f"### {stage_title}\n{stage_detail}".rstrip() if stage_title else "No active stage.",
        "recent-progress": "\n\n".join(recent) or "No recent progress.",
        "notes": "\n".join(f"- {title}" for title in note_titles) or "No matching notes.",
    }
    if section is not None:
        if section not in sections:
            raise MbError(f"unknown start section: {section}", 1)
        output = f"## {section}\n\n{sections[section].rstrip()}\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError(f"start section {section} exceeds 8192 bytes; use recall", 1)
        return output
    protected = _render(current, open_items, "", "", [], [], False)
    if len(protected.encode("utf-8")) > MAX_BYTES:
        current_bytes = len(current.encode("utf-8"))
        item_bytes = len(open_items.encode("utf-8"))
        raise MbError(
            "protected start sections exceed 8192 bytes "
            f"(current={current_bytes}, open-items={item_bytes}); use start --section or recall",
            1,
        )
    trimmed = False
    while len(_render(current, open_items, stage_title, stage_detail, recent, note_titles, trimmed).encode("utf-8")) > MAX_BYTES:
        trimmed = True
        if note_titles:
            note_titles.pop()
        elif recent:
            recent.pop(0)
        elif stage_detail:
            stage_detail = ""
        else:
            raise MbError("start output exceeds 8192 bytes after optional trimming", 1)
    return _render(current, open_items, stage_title, stage_detail, recent, note_titles, trimmed)
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_start -v`

Expected: all start tests pass and output reports `OK`.

- [ ] **Step 5: Commit bounded start**

```bash
git add scripts/mb_lite/start.py tests/test_start.py
git commit -m "feat: add bounded read-only start"
```

### Task 5: Read-Only Lexical Recall

**Files:**
- Create: `scripts/mb_lite/recall.py`
- Create: `tests/test_recall.py`

- [ ] **Step 1: Write failing recall tests**

```python
from pathlib import Path
import tempfile
import unittest

from scripts.mb_lite.recall import expand_result, search_bank


class RecallTest(unittest.TestCase):
    def test_search_is_bounded_stable_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "notes").mkdir()
            note = bank / "notes" / "registry.md"
            note.write_text("# Registry Decision\nUse one central registry.\n", encoding="utf-8")
            (bank / "progress.md").write_text("# Progress\nNo registry work yet.\n", encoding="utf-8")
            before = note.read_bytes()
            results = search_bank(bank, "central registry")
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].result_id), 12)
            self.assertEqual(expand_result(bank, results[0].result_id).path, "notes/registry.md")
            self.assertEqual(note.read_bytes(), before)

    def test_missing_optional_directories_returns_no_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bank = Path(directory)
            (bank / "progress.md").write_text("# Progress\n", encoding="utf-8")
            self.assertEqual(search_bank(bank, "absent"), [])
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_recall -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.recall'`.

- [ ] **Step 3: Implement deterministic lexical recall**

Create `scripts/mb_lite/recall.py`:

```python
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from .errors import MbError


@dataclass(frozen=True)
class RecallResult:
    result_id: str
    path: str
    heading: str
    offset: int
    snippet: str


def _sources(bank: Path) -> list[Path]:
    paths = [bank / "progress.md", bank / "backlog.md"]
    for directory in (bank / "notes", bank / "plans", bank / "plans" / "done"):
        if directory.is_dir():
            paths.extend(sorted(directory.glob("*.md")))
    return [path for path in paths if path.is_file()]


def _chunks(path: Path) -> list[tuple[str, int, str]]:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+)$", text))
    if not matches:
        return [(path.name, 0, text)]
    chunks: list[tuple[str, int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunks.append((match.group(1).strip(), match.start(), text[match.start():end]))
    return chunks


def _id(relative: str, heading: str, offset: int) -> str:
    raw = f"{relative}\0{heading}\0{offset}".encode("utf-8")
    return sha256(raw).hexdigest()[:12]


def search_bank(bank: Path, query: str, limit: int = 10) -> list[RecallResult]:
    terms = [term.casefold() for term in re.findall(r"\w+", query) if len(term) >= 2]
    if not terms:
        raise MbError("recall query must contain a term of at least two characters", 1)
    ranked: list[tuple[int, RecallResult]] = []
    for path in _sources(bank):
        relative = path.relative_to(bank).as_posix()
        for heading, offset, body in _chunks(path):
            folded = body.casefold()
            score = sum(folded.count(term) for term in terms)
            if score == 0:
                continue
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            snippet = " | ".join(lines[:2])[:240]
            ranked.append((score, RecallResult(_id(relative, heading, offset), relative, heading, offset, snippet)))
    ranked.sort(key=lambda item: (-item[0], item[1].path, item[1].offset))
    return [result for _, result in ranked[:limit]]


def expand_result(bank: Path, result_id: str) -> RecallResult:
    for path in _sources(bank):
        relative = path.relative_to(bank).as_posix()
        for heading, offset, body in _chunks(path):
            current = _id(relative, heading, offset)
            if current == result_id:
                return RecallResult(current, relative, heading, offset, body.rstrip())
    raise MbError(f"unknown recall result id: {result_id}", 1)
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_recall -v`

Expected: `Ran 2 tests` and `OK`.

- [ ] **Step 5: Commit recall**

```bash
git add scripts/mb_lite/recall.py tests/test_recall.py
git commit -m "feat: add read-only memory recall"
```

### Task 6: CLI And Minimal Skill Contract

**Files:**
- Create: `scripts/mb.py`
- Create: `tests/test_cli.py`
- Create: `SKILL.md`

- [ ] **Step 1: Write failing end-to-end CLI tests**

```python
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "mb.py"


class CliTest(unittest.TestCase):
    def test_init_start_and_recall_use_central_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            home = root / "memory"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            env = {**os.environ, "MB_LITE_HOME": str(home)}
            initialized = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "init", "--json"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            started = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "start"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertIn("# Memory Bank", started.stdout)
            self.assertLessEqual(len(started.stdout.encode("utf-8")), 8192)

    def test_start_uninitialized_returns_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            env = {**os.environ, "MB_LITE_HOME": str(root / "memory")}
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "start"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not initialized", result.stderr)

    def test_start_section_returns_only_requested_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            home = root / "memory"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            env = {**os.environ, "MB_LITE_HOME": str(home)}
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "init"],
                env=env,
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "start", "--section", "current"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("## current", result.stdout)
            self.assertNotIn("## Open Items", result.stdout)

    def test_init_adopt_requires_confirmation_and_reuses_old_bank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            home = root / "memory"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/old.git"], check=True)
            env = {**os.environ, "MB_LITE_HOME": str(home)}
            first = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "init", "--json"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            old = json.loads(first.stdout)
            subprocess.run(
                ["git", "-C", str(repo), "remote", "set-url", "origin", "git@github.com:example/new.git"],
                check=True,
            )
            registry = home / "registry.json"
            before = registry.read_bytes()
            refused = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "init", "--adopt", old["repository_key"]],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(registry.read_bytes(), before)
            adopted = subprocess.run(
                [
                    "python3", str(CLI), "--project-root", str(repo), "init",
                    "--adopt", old["repository_key"], "--confirm-adopt", "--json",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(adopted.stdout)["bank_path"], old["bank_path"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_cli -v`

Expected: Python exits non-zero because `scripts/mb.py` does not exist.

- [ ] **Step 3: Implement the CLI dispatcher**

Create `scripts/mb.py` with executable mode:

```python
#!/usr/bin/env python3
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from mb_lite.bank import initialize_bank, validate_bank
from mb_lite.errors import MbError
from mb_lite.identity import resolve_identity
from mb_lite.recall import expand_result, search_bank
from mb_lite.registry import RegistryStore
from mb_lite.start import build_start_context


DEFAULT_HOME = Path("/Users/wangxiaoguang/Docs/obsidian/memory-bank")


def _home() -> Path:
    return Path(os.environ.get("MB_LITE_HOME", str(DEFAULT_HOME))).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mb")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("--json", action="store_true")
    init.add_argument("--adopt", metavar="OLD_REPOSITORY_KEY")
    init.add_argument("--confirm-adopt", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("--json", action="store_true")
    start.add_argument(
        "--section",
        choices=("current", "open-items", "active-stage", "recent-progress", "notes"),
    )
    recall = sub.add_parser("recall")
    recall.add_argument("query", nargs="?")
    recall.add_argument("--expand")
    recall.add_argument("--json", action="store_true")
    return parser


def _bank(args: argparse.Namespace, store: RegistryStore):
    identity = resolve_identity(args.project_root)
    return identity, store.lookup(identity.key).bank_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = RegistryStore(_home())
    if args.action == "init":
        identity = resolve_identity(args.project_root)
        if args.adopt and not args.confirm_adopt:
            raise MbError("init --adopt requires --confirm-adopt after explicit user confirmation", 1)
        if args.confirm_adopt and not args.adopt:
            raise MbError("--confirm-adopt requires --adopt OLD_REPOSITORY_KEY", 1)
        if args.adopt:
            old = store.lookup(args.adopt)
            validate_bank(old.bank_path)
            entry = store.adopt(identity, args.adopt)
        else:
            initialize_bank(store.bank_path(identity))
            entry = store.register(identity)
        payload = {"repository_key": identity.key, "project_id": entry.project_id, "bank_path": str(entry.bank_path)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Initialized {entry.bank_path}")
        return 0
    identity, bank = _bank(args, store)
    validate_bank(bank)
    if args.action == "start":
        context = build_start_context(bank, args.section)
        print(json.dumps({"repository_key": identity.key, "bank_path": str(bank), "context": context}, ensure_ascii=False) if args.json else context, end="" if not args.json else "\n")
        return 0
    if args.expand:
        result = expand_result(bank, args.expand)
        payload = asdict(result)
        print(json.dumps(payload, ensure_ascii=False) if args.json else result.snippet)
        return 0
    results = search_bank(bank, args.query or "")
    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
    else:
        for result in results:
            print(f"{result.result_id} | {result.path} | {result.heading} | {result.snippet}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MbError as exc:
        print(f"mb: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
```

Create `SKILL.md`:

```markdown
---
name: mb
description: Explicit project memory. Use only when the user invokes mb init, start, done, or recall.
slash: true
---

# Memory Bank Lite

This skill never runs automatically.

For `init`, run `python3 <skill-dir>/scripts/mb.py --project-root "$PWD" init`.
When the remote changed and the user asks to reuse an old Bank, show the old
repository key and Bank path, obtain explicit confirmation, then run
the same CLI with `init --adopt <old-key> --confirm-adopt`. Never supply
`--confirm-adopt` without that confirmation.
For `start`, run the same CLI with `start` and return its bounded context.
For `recall <query>`, run the same CLI with `recall <query>`.
For `done`, follow the confirmed-write workflow added by the next implementation plan.

Do not initialize, read, or write a Bank unless the user explicitly invokes one of these actions.
```

- [ ] **Step 4: Run the full milestone suite and verify GREEN**

Run: `chmod +x scripts/mb.py && python3 -m unittest discover -s tests -v`

Expected: every identity, registry, bank, start, recall, and CLI test passes.

- [ ] **Step 5: Verify syntax and runtime help**

Run: `python3 -m compileall -q scripts tests && python3 scripts/mb.py --help`

Expected: compile exits `0`; help lists `init`, `start`, and `recall`.

- [ ] **Step 6: Commit the working read-only core**

```bash
git add SKILL.md scripts tests
git commit -m "feat: ship memory bank lite read path"
```

## Milestone Verification

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
git status --short
```

Expected:

- all tests pass;
- compile exits `0`;
- the working tree is clean;
- `init` is the only write action;
- `start` and `recall` leave fixture Banks byte-identical.
