# Memory Bank Lite Migration And Host Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate legacy per-host registrations into one central registry, safely remove Memory Bank-managed host integration, install the Lite skill from source, and verify one shared Bank across Codex, Claude Code, and OpenCode.

**Architecture:** Migration is plan-first and dry-run by default. A transaction manifest backs up every modified configuration file before apply, cleanup requires a strong ownership signal, rollback restores configuration bytes, and the new installer creates only canonical skill symlinks plus a thin Claude command wrapper. The accepted OpenCode 1.4.10 host discovers `slash: true` skills from `~/.agents/skills`, so it receives no duplicate wrapper.

**Tech Stack:** Python 3.11+ standard library, Bash 3.2, `unittest`, JSON manifests, SHA-256, filesystem symlinks.

---

## Prerequisites

Complete these plans first:

1. `2026-07-10-memory-bank-lite-core-read-path.md`
2. `2026-07-10-memory-bank-lite-confirmed-write-path.md`

The real-machine `--apply` step remains behind an explicit human confirmation
after review of dry-run output.

## File Structure

- Create `scripts/mb_lite/migration.py`: legacy registry discovery, remote identity conversion, Bank digest, and conflict planning.
- Create `scripts/mb_lite/cleanup.py`: marker-safe text/JSON cleanup and project-manifest inventory.
- Create `scripts/mb_lite/transaction.py`: backup manifest, apply helpers, and rollback.
- Create `scripts/migrate_cleanup.py`: migration CLI orchestration.
- Create `migrate-cleanup.sh`: Bash entry point into the Python migration CLI.
- Create `install.sh`: source symlink and thin Claude command-wrapper installation.
- Create `uninstall.sh`: remove only Lite-owned symlinks and wrappers.
- Create `tests/test_migration.py`: registry merge and Bank conflict contracts.
- Create `tests/test_cleanup.py`: ownership-safe cleanup transforms.
- Create `tests/test_transaction.py`: backup and rollback byte identity.
- Create `tests/test_install.py`: isolated-HOME source install/uninstall contracts.
- Create `tests/test_migration_e2e.py`: full legacy fixture dry-run/apply/rollback.
- Create `docs/local-acceptance.md`: exact real-machine validation commands.

### Task 1: Legacy Registry Inventory And Conflict Planning

**Files:**
- Create: `scripts/mb_lite/migration.py`
- Create: `tests/test_migration.py`

- [x] **Step 1: Write failing legacy inventory tests**

```python
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.migration import legacy_registry_paths, plan_migration, tree_digest


class MigrationTest(unittest.TestCase):
    def test_legacy_registry_paths_cover_three_hosts(self) -> None:
        home = Path("/tmp/home")
        self.assertEqual(
            legacy_registry_paths(home),
            (
                home / ".codex/memory-bank/registry.json",
                home / ".claude/memory-bank/registry.json",
                home / ".config/opencode/memory-bank/registry.json",
            ),
        )

    def test_identical_host_records_merge_to_one_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            bank = root / "shared" / ".memory-bank"
            bank.mkdir(parents=True)
            for name in ("status.md", "checklist.md", "progress.md"):
                (bank / name).write_text(f"# {name}\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            for registry in legacy_registry_paths(root):
                registry.parent.mkdir(parents=True)
                registry.write_text(json.dumps({"projects": {str(repo): {"bank_path": str(bank)}}}), encoding="utf-8")
            plan = plan_migration(root, root / "central")
            self.assertEqual(tuple(plan.projects), ("github.com/example/repo",))
            self.assertEqual(plan.projects["github.com/example/repo"].source_banks, (bank.resolve(),))
            self.assertEqual(plan.projects["github.com/example/repo"].known_roots, (repo.resolve(),))

    def test_different_banks_for_same_remote_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            registries = legacy_registry_paths(root)
            for index, registry in enumerate(registries[:2]):
                bank = root / f"bank-{index}" / ".memory-bank"
                bank.mkdir(parents=True)
                for name in ("status.md", "checklist.md", "progress.md"):
                    (bank / name).write_text(f"{name}-{index}\n", encoding="utf-8")
                registry.parent.mkdir(parents=True)
                registry.write_text(json.dumps({"projects": {str(repo): {"bank_path": str(bank)}}}), encoding="utf-8")
            with self.assertRaisesRegex(MbError, "different Bank content"):
                plan_migration(root, root / "central")
```

- [x] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_migration -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.migration'`.

- [x] **Step 3: Implement inventory, full-tree digest, and merge planning**

Create `scripts/mb_lite/migration.py`:

```python
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .bank import validate_bank
from .errors import MbError
from .identity import RepositoryIdentity, resolve_identity


@dataclass(frozen=True)
class PlannedProject:
    identity: RepositoryIdentity
    source_banks: tuple[Path, ...]
    target_bank: Path
    known_roots: tuple[Path, ...]


@dataclass(frozen=True)
class MigrationPlan:
    projects: dict[str, PlannedProject]
    legacy_registries: tuple[Path, ...]


def legacy_registry_paths(home: Path) -> tuple[Path, ...]:
    return (
        home / ".codex/memory-bank/registry.json",
        home / ".claude/memory-bank/registry.json",
        home / ".config/opencode/memory-bank/registry.json",
    )


def tree_digest(bank: Path) -> str:
    digest = sha256()
    for path in sorted(value for value in bank.rglob("*") if value.is_file() and value.name != ".mb-lite.lock"):
        digest.update(path.relative_to(bank).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _records(path: Path) -> list[tuple[Path, Path]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt legacy registry: {path}", 4) from exc
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise MbError(f"corrupt legacy registry projects: {path}", 4)
    result: list[tuple[Path, Path]] = []
    for root, raw in projects.items():
        if not isinstance(raw, dict) or not isinstance(raw.get("bank_path"), str):
            raise MbError(f"invalid legacy registry entry for {root}", 4)
        result.append((Path(root), Path(raw["bank_path"])))
    return result


def plan_migration(home: Path, central_home: Path) -> MigrationPlan:
    grouped: dict[str, tuple[RepositoryIdentity, list[Path], list[Path]]] = {}
    registries = tuple(path for path in legacy_registry_paths(home) if path.exists())
    for registry in registries:
        for root, raw_bank in _records(registry):
            if not root.is_dir():
                raise MbError(f"legacy project root does not exist: {root}", 4)
            identity = resolve_identity(root)
            bank = validate_bank(raw_bank.resolve())
            current = grouped.setdefault(identity.key, (identity, [], []))
            if bank not in current[1]:
                current[1].append(bank)
            if identity.root not in current[2]:
                current[2].append(identity.root)
    planned: dict[str, PlannedProject] = {}
    for key, (identity, banks, roots) in grouped.items():
        digests = {tree_digest(bank) for bank in banks}
        if len(digests) > 1:
            raise MbError(f"repository {key} has different Bank content across legacy registries", 4)
        target = central_home / "projects" / identity.project_id / ".memory-bank"
        planned[key] = PlannedProject(identity, tuple(sorted(banks)), target, tuple(sorted(roots)))
    return MigrationPlan(planned, registries)
```

- [x] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m unittest tests.test_migration -v`

Expected: `Ran 3 tests` and `OK`.

- [x] **Step 5: Commit migration planning**

```bash
git add scripts/mb_lite/migration.py tests/test_migration.py
git commit -m "feat: plan legacy memory migration"
```

### Task 2: Ownership-Safe Cleanup Transforms

**Files:**
- Create: `scripts/mb_lite/cleanup.py`
- Create: `tests/test_cleanup.py`

- [x] **Step 1: Write failing marker and JSON cleanup tests**

```python
from pathlib import Path
import json
import tempfile
import unittest

from scripts.mb_lite.cleanup import clean_hooks_json, remove_opencode_plugin, strip_known_blocks


class CleanupTest(unittest.TestCase):
    def test_strip_known_blocks_preserves_user_prefix_and_suffix(self) -> None:
        source = "user-before\n<!-- memory-bank-codex:start -->\nmanaged\n<!-- memory-bank-codex:end -->\nuser-after\n"
        cleaned, changed = strip_known_blocks(source)
        self.assertTrue(changed)
        self.assertEqual(cleaned, "user-before\n\nuser-after\n")

    def test_strip_unpaired_marker_refuses_to_change_content(self) -> None:
        source = "user\n<!-- memory-bank:start -->\nmanaged\n"
        cleaned, changed = strip_known_blocks(source)
        self.assertFalse(changed)
        self.assertEqual(cleaned, source)

    def test_clean_hooks_removes_only_memory_bank_commands(self) -> None:
        source = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "echo user"}]},
                    {"hooks": [{"type": "command", "command": "mb-session-turn.sh # [memory-bank-skill]"}]},
                ]
            },
            "theme": "dark",
        }
        cleaned = clean_hooks_json(source)
        self.assertEqual(cleaned["theme"], "dark")
        self.assertEqual(cleaned["hooks"]["Stop"], [{"hooks": [{"type": "command", "command": "echo user"}]}])

    def test_remove_opencode_plugin_preserves_unrelated_plugins(self) -> None:
        source = {"plugin": ["./.opencode/plugins/memory-bank.js", "user-plugin"], "theme": "dark"}
        cleaned = remove_opencode_plugin(source, "./.opencode/plugins/memory-bank.js")
        self.assertEqual(cleaned, {"plugin": ["user-plugin"], "theme": "dark"})
```

- [x] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_cleanup -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.cleanup'`.

- [x] **Step 3: Implement paired-marker and hook ownership transforms**

Create `scripts/mb_lite/cleanup.py`:

```python
from copy import deepcopy
import re
from typing import Any

from .errors import MbError


MARKER_PAIRS = (
    ("<!-- memory-bank-codex:start -->", "<!-- memory-bank-codex:end -->"),
    ("# [MEMORY-BANK-SKILL]", "<!-- /memory-bank-skill -->"),
    ("<!-- memory-bank:start -->", "<!-- memory-bank:end -->"),
    ("<!-- memory-bank-cursor:start -->", "<!-- memory-bank-cursor:end -->"),
    ("<!-- memory-bank-pi:start -->", "<!-- memory-bank-pi:end -->"),
)


def strip_known_blocks(source: str) -> tuple[str, bool]:
    result = source
    changed = False
    for start, end in MARKER_PAIRS:
        starts = result.count(start)
        ends = result.count(end)
        if starts != ends:
            continue
        if starts == 0:
            continue
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
        result, count = pattern.subn("", result)
        changed = changed or count > 0
    return result, changed


def _managed_hook(hook: Any) -> bool:
    return isinstance(hook, dict) and "[memory-bank-skill]" in str(hook.get("command", ""))


def clean_hooks_json(source: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(source)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                kept.append(entry)
                continue
            inner = [hook for hook in entry["hooks"] if not _managed_hook(hook)]
            if inner:
                updated = dict(entry)
                updated["hooks"] = inner
                kept.append(updated)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result


def remove_opencode_plugin(source: dict[str, Any], plugin_ref: str) -> dict[str, Any]:
    result = deepcopy(source)
    plugins = result.get("plugin")
    if isinstance(plugins, list):
        kept = [value for value in plugins if value != plugin_ref]
        if kept:
            result["plugin"] = kept
        else:
            result.pop("plugin", None)
    return result
```

- [x] **Step 4: Add project manifest containment tests and implementation**

Replace the cleanup import at module scope with these imports:

```python
from scripts.mb_lite.cleanup import clean_hooks_json, manifest_owned_files, remove_opencode_plugin, strip_known_blocks
from scripts.mb_lite.errors import MbError
```

Add this method at four-space indentation inside `CleanupTest`, immediately
after `test_clean_hooks_removes_only_memory_bank_commands`:

```python
    def test_manifest_files_must_stay_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / ".codex/.mb-manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"files": [str(root / ".codex/hooks.json"), "/tmp/escape"]}))
            with self.assertRaisesRegex(MbError, "manifest path escapes project"):
                manifest_owned_files(root, manifest)
```

Append to `cleanup.py`:

```python
from pathlib import Path
import json


def manifest_owned_files(project: Path, manifest: Path) -> tuple[Path, ...]:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt adapter manifest: {manifest}", 4) from exc
    files = data.get("files", [])
    if not isinstance(files, list) or any(not isinstance(value, str) for value in files):
        raise MbError(f"corrupt adapter manifest files: {manifest}", 4)
    root = project.resolve()
    result: list[Path] = []
    for raw in files:
        lexical = Path(str(raw))
        if not lexical.is_absolute():
            lexical = project / lexical
        path = lexical.parent.resolve() / lexical.name
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise MbError(f"manifest path escapes project: {path}", 4) from exc
        result.append(path)
    return tuple(result)
```

- [x] **Step 5: Run tests and commit cleanup transforms**

Run: `python3 -m unittest tests.test_cleanup -v`

Expected: all five cleanup tests pass.

```bash
git add scripts/mb_lite/cleanup.py tests/test_cleanup.py
git commit -m "feat: add ownership-safe cleanup transforms"
```

### Task 3: Backup Transaction And Rollback

**Files:**
- Create: `scripts/mb_lite/transaction.py`
- Create: `tests/test_transaction.py`

- [x] **Step 1: Write failing backup and rollback test**

```python
from pathlib import Path
from hashlib import sha256
import tempfile
import unittest

from scripts.mb_lite.transaction import BackupTransaction


class TransactionTest(unittest.TestCase):
    def test_rollback_restores_original_bytes_and_removes_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            existing = root / "config.json"
            created = root / "new.json"
            existing.write_bytes(b"original\n")
            transaction = BackupTransaction.create(state, "20260710-120000")
            transaction.capture(existing, "clean hooks")
            transaction.capture_missing(created, "create registry")
            existing.write_bytes(b"changed\n")
            created.write_bytes(b"new\n")
            transaction.finish()
            BackupTransaction.rollback(state / "20260710-120000")
            self.assertEqual(existing.read_bytes(), b"original\n")
            self.assertFalse(created.exists())

    def test_rollback_restores_removed_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            source = root / "source"
            link = root / "skill"
            source.mkdir()
            link.symlink_to(source, target_is_directory=True)
            transaction = BackupTransaction.create(state, "20260710-120001")
            transaction.capture(link, "remove legacy skill link")
            link.unlink()
            transaction.finish()
            BackupTransaction.rollback(state / "20260710-120001")
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), source.resolve())
```

- [x] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest tests.test_transaction -v`

Expected: `ModuleNotFoundError: No module named 'scripts.mb_lite.transaction'`.

- [x] **Step 3: Implement transaction manifests and rollback**

Create `scripts/mb_lite/transaction.py`:

```python
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path

from .errors import MbError


@dataclass
class BackupTransaction:
    directory: Path
    records: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def create(cls, state_root: Path, timestamp: str) -> "BackupTransaction":
        directory = state_root / timestamp
        directory.mkdir(parents=True, exist_ok=False)
        return cls(directory)

    def capture(self, path: Path, action: str) -> None:
        if path.is_symlink():
            target = os.readlink(path)
            self.records.append({
                "path": str(path),
                "backup": None,
                "sha256": sha256(target.encode("utf-8")).hexdigest(),
                "target": target,
                "action": action,
                "kind": "symlink",
            })
            return
        data = path.read_bytes()
        name = f"{len(self.records):04d}-{path.name}"
        backup = self.directory / name
        backup.write_bytes(data)
        self.records.append({
            "path": str(path),
            "backup": name,
            "sha256": sha256(data).hexdigest(),
            "action": action,
            "kind": "file",
        })

    def capture_missing(self, path: Path, action: str) -> None:
        self.records.append({"path": str(path), "backup": None, "sha256": None, "action": action, "kind": "missing"})

    def finish(self) -> None:
        (self.directory / "manifest.json").write_text(
            json.dumps({"version": 1, "records": self.records}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def rollback(directory: Path) -> None:
        try:
            data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MbError(f"invalid rollback manifest: {directory}", 4) from exc
        for record in reversed(data["records"]):
            path = Path(record["path"])
            if record["kind"] == "file":
                backup = directory / record["backup"]
                content = backup.read_bytes()
                if sha256(content).hexdigest() != record["sha256"]:
                    raise MbError(f"backup checksum mismatch: {backup}", 4)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            elif record["kind"] == "symlink":
                target = str(record["target"])
                if sha256(target.encode("utf-8")).hexdigest() != record["sha256"]:
                    raise MbError(f"backup checksum mismatch: symlink {path}", 4)
                if path.is_symlink() or path.is_file():
                    path.unlink()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target)
            elif record["kind"] == "missing" and (path.is_symlink() or path.is_file()):
                path.unlink()
```

- [x] **Step 4: Run the test and verify GREEN**

Run: `python3 -m unittest tests.test_transaction -v`

Expected: `Ran 2 tests` and `OK`.

- [x] **Step 5: Commit backup transactions**

```bash
git add scripts/mb_lite/transaction.py tests/test_transaction.py
git commit -m "feat: add migration rollback transactions"
```

### Task 4: Source Installer And Uninstaller

**Files:**
- Create: `install.sh`
- Create: `uninstall.sh`
- Create: `commands/mb.md`
- Create: `tests/test_install.py`

- [x] **Step 1: Write failing isolated-HOME installer tests**

```python
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class InstallTest(unittest.TestCase):
    def test_install_creates_only_canonical_and_claude_skill_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {**os.environ, "HOME": str(home), "MB_LITE_SOURCE": str(REPO)}
            result = subprocess.run(["bash", str(REPO / "install.sh")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((home / ".agents/skills/mb").resolve(), REPO.resolve())
            self.assertEqual((home / ".claude/skills/mb").resolve(), REPO.resolve())
            self.assertFalse((home / ".codex/skills/mb").exists())
            self.assertFalse((home / ".config/opencode/skills/mb").exists())
            self.assertFalse((home / ".config/opencode/commands/mb.md").exists())
            self.assertFalse((home / ".codex/AGENTS.md").exists())

    def test_uninstall_preserves_registry_and_bank_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            data = home / "memory"
            data.mkdir()
            (data / "registry.json").write_text("{}\n")
            env = {**os.environ, "HOME": str(home), "MB_LITE_SOURCE": str(REPO), "MB_LITE_HOME": str(data)}
            subprocess.run(["bash", str(REPO / "install.sh")], env=env, check=True)
            subprocess.run(["bash", str(REPO / "uninstall.sh")], env=env, check=True)
            self.assertTrue((data / "registry.json").is_file())
            self.assertFalse((home / ".agents/skills/mb").exists())
```

- [x] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_install -v`

Expected: both tests fail because `install.sh` and `uninstall.sh` do not exist.

- [x] **Step 3: Create the thin command wrapper**

Create `commands/mb.md` with six lines:

```markdown
---
description: Explicit Memory Bank Lite action
---
Load the `mb` skill and execute this action: $ARGUMENTS
Do not run any Memory Bank action implicitly.
```

- [x] **Step 4: Implement source-only installation**

Create executable `install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SOURCE="${MB_LITE_SOURCE:-$(cd "$(dirname "$0")" && pwd)}"
AGENTS_SKILL="$HOME/.agents/skills/mb"
CLAUDE_SKILL="$HOME/.claude/skills/mb"

link_skill() {
  target="$1"
  source="$2"
  mkdir -p "$(dirname "$target")"
  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -L "$target" ] && [ "$(cd "$(dirname "$target")" && cd "$(readlink "$target")" 2>/dev/null && pwd -P)" = "$(cd "$source" && pwd -P)" ] && return 0
    echo "mb install: refusing to replace existing $target" >&2
    exit 1
  fi
  ln -s "$source" "$target"
}

link_skill "$AGENTS_SKILL" "$SOURCE"
link_skill "$CLAUDE_SKILL" "$AGENTS_SKILL"

mkdir -p "$HOME/.claude/commands"
cp "$SOURCE/commands/mb.md" "$HOME/.claude/commands/mb.md"

echo "mb installed from $SOURCE"
```

Do not create a Codex prompt or an OpenCode command wrapper. The local
acceptance task verifies that OpenCode 1.4.10 exposes the installed
`slash: true` skill directly as `/mb`; failure is an acceptance blocker rather
than permission to add a second skill copy.

- [x] **Step 5: Implement ownership-safe uninstall**

Create executable `uninstall.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SOURCE="${MB_LITE_SOURCE:-$(cd "$(dirname "$0")" && pwd)}"

remove_link() {
  target="$1"
  [ -L "$target" ] || return 0
  resolved="$(cd "$(dirname "$target")" && cd "$(readlink "$target")" 2>/dev/null && pwd -P)"
  [ "$resolved" = "$(cd "$SOURCE" && pwd -P)" ] || return 0
  rm "$target"
}

remove_wrapper() {
  target="$1"
  [ -f "$target" ] || return 0
  grep -q '^description: Explicit Memory Bank Lite action$' "$target" || return 0
  rm "$target"
}

remove_link "$HOME/.claude/skills/mb"
remove_link "$HOME/.agents/skills/mb"
remove_wrapper "$HOME/.claude/commands/mb.md"

echo "mb integration removed; registry and Banks preserved"
```

- [x] **Step 6: Run tests and commit source installation**

Run: `chmod +x install.sh uninstall.sh && python3 -m unittest tests.test_install -v`

Expected: both tests pass.

```bash
git add install.sh uninstall.sh commands/mb.md tests/test_install.py
git commit -m "feat: install memory skill from source"
```

### Task 5: Dry-Run, Apply, And Rollback Orchestration

**Files:**
- Create: `scripts/migrate_cleanup.py`
- Create: `migrate-cleanup.sh`
- Create: `tests/test_migration_e2e.py`
- Modify: `scripts/mb_lite/registry.py`

- [x] **Step 1: Write a failing end-to-end dry-run and rollback test**

```python
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "migrate-cleanup.sh"


class MigrationE2ETest(unittest.TestCase):
    def test_dry_run_is_byte_identical_and_apply_is_rollback_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            central = root / "central"
            repo = root / "repo"
            home.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            bank = central / "projects" / "repo-legacy" / ".memory-bank"
            bank.mkdir(parents=True)
            for name in ("status.md", "checklist.md", "progress.md"):
                (bank / name).write_text(f"# {name}\n", encoding="utf-8")
            registry = home / ".codex/memory-bank/registry.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"projects": {str(repo): {"bank_path": str(bank)}}}))
            plugin = repo / ".opencode/plugins/memory-bank.js"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("// memory-bank: managed plugin\n", encoding="utf-8")
            adapter_manifest = repo / ".opencode/.mb-manifest.json"
            adapter_manifest.write_text(json.dumps({
                "files": [str(plugin)],
                "plugin_ref": "./.opencode/plugins/memory-bank.js",
            }))
            opencode = repo / "opencode.json"
            opencode.write_text(json.dumps({
                "plugin": ["./.opencode/plugins/memory-bank.js", "user-plugin"],
                "theme": "dark",
            }))
            agents = home / ".codex/AGENTS.md"
            agents.parent.mkdir(parents=True, exist_ok=True)
            agents.write_text("user\n<!-- memory-bank-codex:start -->\nmanaged\n<!-- memory-bank-codex:end -->\n")
            env = {**os.environ, "HOME": str(home), "MB_LITE_HOME": str(central), "MB_LITE_SOURCE": str(REPO)}
            before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
            plugin_before = plugin.read_bytes()
            opencode_before = opencode.read_bytes()
            dry = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn(str(central / "registry.json"), dry.stdout)
            self.assertEqual(before, {path: path.read_bytes() for path in home.rglob("*") if path.is_file()})
            self.assertEqual(plugin.read_bytes(), plugin_before)
            self.assertEqual(opencode.read_bytes(), opencode_before)
            applied = subprocess.run(["bash", str(SCRIPT), "--apply", "--timestamp", "20260710-120000"], env=env, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertNotIn("memory-bank-codex:start", agents.read_text())
            self.assertTrue((central / "registry.json").is_file())
            self.assertFalse(plugin.exists())
            self.assertEqual(json.loads(opencode.read_text())["plugin"], ["user-plugin"])
            rolled = subprocess.run(["bash", str(SCRIPT), "--rollback", "20260710-120000"], env=env, capture_output=True, text=True)
            self.assertEqual(rolled.returncode, 0, rolled.stderr)
            self.assertEqual(agents.read_bytes(), before[agents])
            self.assertEqual(registry.read_bytes(), before[registry])
            self.assertEqual(plugin.read_bytes(), plugin_before)
            self.assertEqual(opencode.read_bytes(), opencode_before)
```

- [x] **Step 2: Run the test and verify RED**

Run: `python3 -m unittest tests.test_migration_e2e -v`

Expected: the test fails because `migrate-cleanup.sh` does not exist.

- [x] **Step 3: Add a public registry replacement method**

Add to `RegistryStore` in `scripts/mb_lite/registry.py`:

```python
def replace_projects(self, projects: dict[str, dict[str, object]]) -> None:
    with self.locked():
        current = self._load(allow_missing=True)
        current["version"] = 1
        current["projects_root"] = str(self.projects_root)
        current["projects"] = projects
        self._write(current)
```

- [x] **Step 4: Implement the migration CLI with dry-run default**

Create `scripts/migrate_cleanup.py` with this complete implementation:

```python
#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from mb_lite.cleanup import (
    clean_hooks_json,
    manifest_owned_files,
    remove_opencode_plugin,
    strip_known_blocks,
)
from mb_lite.errors import MbError
from mb_lite.migration import MigrationPlan, plan_migration, tree_digest
from mb_lite.registry import RegistryStore
from mb_lite.transaction import BackupTransaction


@dataclass(frozen=True)
class CleanupAction:
    kind: str
    path: Path
    reason: str
    value: str | None = None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="migrate-cleanup.sh")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", metavar="ID")
    result.add_argument("--timestamp", help="deterministic transaction ID for tests")
    return result


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt {label}: {path}", 4) from exc
    if not isinstance(value, dict):
        raise MbError(f"corrupt {label}: {path}", 4)
    return value


def _legacy_install_manifests(home: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    override = os.environ.get("MB_LEGACY_MANIFEST")
    if override:
        candidates.append(Path(override).expanduser())
    for skill in (
        home / ".claude/skills/skill-memory-bank",
        home / ".claude/skills/memory-bank",
        home / ".codex/skills/memory-bank",
        home / ".config/opencode/skills/memory-bank",
    ):
        if skill.exists() or skill.is_symlink():
            candidates.append(skill.resolve() / ".installed-manifest.json")
    unique = {path.resolve() for path in candidates if path.is_file()}
    return tuple(sorted(unique))


def _manifest_actions(
    home: Path,
    project: Path,
    manifest: Path,
    shared: set[Path],
) -> list[CleanupAction]:
    data = _load_object(manifest, "adapter manifest")
    actions = [
        CleanupAction("remove-owned", path, f"owned by {manifest}")
        for path in manifest_owned_files(project, manifest)
        if path not in shared
    ]
    plugin_ref = data.get("plugin_ref")
    if isinstance(plugin_ref, str) and plugin_ref:
        config = project / "opencode.json"
        if config.is_file():
            actions.append(CleanupAction("opencode-plugin", config, f"plugin owned by {manifest}", plugin_ref))
    if manifest.parent == project or manifest.is_relative_to(project):
        actions.append(CleanupAction("remove-owned", manifest, "consumed legacy ownership manifest"))
    return actions


def build_cleanup_actions(home: Path, plan: MigrationPlan) -> tuple[CleanupAction, ...]:
    instruction_paths = (
        home / ".codex/AGENTS.md",
        home / ".claude/CLAUDE.md",
        home / ".config/opencode/AGENTS.md",
        home / ".cursor/AGENTS.md",
        home / ".pi/agent/AGENTS.md",
    )
    settings = home / ".claude/settings.json"
    shared = {path for path in instruction_paths}
    shared.add(settings)
    actions: list[CleanupAction] = [
        CleanupAction("strip-text", path, "paired Memory Bank marker")
        for path in instruction_paths
        if path.is_file()
    ]
    if settings.is_file():
        actions.append(CleanupAction("clean-hooks", settings, "[memory-bank-skill] hook marker"))
    actions.extend(
        CleanupAction("remove-owned", path, "successfully imported legacy registry")
        for path in plan.legacy_registries
    )

    roots = sorted({root for item in plan.projects.values() for root in item.known_roots})
    for root in roots:
        agents = root / "AGENTS.md"
        if agents.is_file():
            actions.append(CleanupAction("strip-text", agents, "paired project Memory Bank marker"))
        for manifest in (
            root / ".codex/.mb-manifest.json",
            root / ".opencode/.mb-manifest.json",
            root / ".git/mb-hooks-manifest.json",
            root / ".git/hooks/.memory-bank-manifest.json",
        ):
            if manifest.is_file():
                actions.extend(_manifest_actions(home, root, manifest, shared))

    for manifest in _legacy_install_manifests(home):
        actions.extend(_manifest_actions(home, home, manifest, shared))
    cursor_manifest = home / ".cursor/.mb-manifest.json"
    if cursor_manifest.is_file():
        actions.extend(_manifest_actions(home, home, cursor_manifest, shared))

    deduplicated: dict[tuple[str, Path, str | None], CleanupAction] = {}
    for action in actions:
        deduplicated[(action.kind, action.path, action.value)] = action
    return tuple(sorted(deduplicated.values(), key=lambda item: (str(item.path), item.kind)))


def render_plan(plan: MigrationPlan, actions: tuple[CleanupAction, ...], central: Path) -> str:
    lines = [
        "Memory Bank Lite migration plan",
        "",
        f"Central registry: {central / 'registry.json'}",
        "",
        "Banks:",
    ]
    if not plan.projects:
        lines.append("- none")
    for key, item in sorted(plan.projects.items()):
        lines.append(f"- {key}: {item.source_banks[0]} -> {item.target_bank}")
    lines.extend(("", "Cleanup:"))
    if not actions:
        lines.append("- none")
    for action in actions:
        suffix = f" ({action.value})" if action.value else ""
        lines.append(f"- {action.kind}: {action.path}{suffix} - {action.reason}")
    return "\n".join(lines)


def _existing_projects(central: Path) -> dict[str, dict[str, object]]:
    registry = central / "registry.json"
    if not registry.exists():
        return {}
    data = _load_object(registry, "central registry")
    projects = data.get("projects")
    if data.get("version") != 1 or not isinstance(projects, dict):
        raise MbError(f"corrupt central registry schema: {registry}", 4)
    return {str(key): dict(value) for key, value in projects.items() if isinstance(value, dict)}


def apply_migration(plan: MigrationPlan, central: Path, transaction: BackupTransaction) -> None:
    projects = _existing_projects(central)
    for key, item in sorted(plan.projects.items()):
        source = item.source_banks[0]
        if item.target_bank.exists():
            if tree_digest(source) != tree_digest(item.target_bank):
                raise MbError(f"target Bank differs for repository {key}: {item.target_bank}", 4)
        else:
            item.target_bank.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, item.target_bank, dirs_exist_ok=False)
        projects[key] = {
            "project_id": item.identity.project_id,
            "remote": item.identity.key if item.identity.remote else None,
            "bank_path": str(item.target_bank),
            "known_roots": [str(root) for root in item.known_roots],
        }
    registry = central / "registry.json"
    if registry.exists():
        transaction.capture(registry, "replace central registry")
    else:
        transaction.capture_missing(registry, "create central registry")
    RegistryStore(central).replace_projects(projects)


def _atomic_text(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _remove_owned(path: Path, transaction: BackupTransaction, reason: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        raise MbError(f"refusing to remove owned directory without symlink proof: {path}", 4)
    transaction.capture(path, reason)
    path.unlink()


def apply_cleanup(actions: tuple[CleanupAction, ...], transaction: BackupTransaction) -> None:
    for action in actions:
        path = action.path
        if action.kind == "remove-owned":
            _remove_owned(path, transaction, action.reason)
            continue
        if not path.is_file():
            continue
        if action.kind == "strip-text":
            source = path.read_text(encoding="utf-8")
            result, changed = strip_known_blocks(source)
            if not changed:
                continue
        else:
            source_object = _load_object(path, "cleanup JSON")
            if action.kind == "clean-hooks":
                result_object = clean_hooks_json(source_object)
            elif action.kind == "opencode-plugin" and action.value is not None:
                result_object = remove_opencode_plugin(source_object, action.value)
            else:
                raise MbError(f"unknown cleanup action: {action.kind}", 4)
            if result_object == source_object:
                continue
            result = json.dumps(result_object, ensure_ascii=False, indent=2) + "\n"
        transaction.capture(path, action.reason)
        _atomic_text(path, result)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    home = Path.home()
    central = Path(os.environ.get("MB_LITE_HOME", "/Users/wangxiaoguang/Docs/obsidian/memory-bank"))
    state = home / ".local/state/mb-migration"
    if args.rollback:
        BackupTransaction.rollback(state / args.rollback)
        return 0
    plan = plan_migration(home, central)
    actions = build_cleanup_actions(home, plan)
    print(render_plan(plan, actions, central))
    if not args.apply:
        return 0
    transaction = BackupTransaction.create(state, args.timestamp or utc_timestamp())
    apply_migration(plan, central, transaction)
    apply_cleanup(actions, transaction)
    transaction.finish()
    print(f"Applied transaction {transaction.directory.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MbError as exc:
        print(f"mb migration: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
```

- [x] **Step 5: Create the Bash entry point**

Create executable `migrate-cleanup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$ROOT/scripts/migrate_cleanup.py" "$@"
```

- [x] **Step 6: Run migration tests and commit orchestration**

Run:

```bash
chmod +x scripts/migrate_cleanup.py migrate-cleanup.sh
python3 -m unittest tests.test_migration_e2e -v
python3 -m unittest discover -s tests -v
```

Expected: dry-run is byte-identical, apply cleans only the managed block, and
rollback restores the original AGENTS bytes.

```bash
git add scripts/mb_lite/registry.py scripts/migrate_cleanup.py migrate-cleanup.sh tests/test_migration_e2e.py
git commit -m "feat: migrate and clean legacy memory installs"
```

### Task 6: Real-Machine Acceptance Runbook

**Files:**
- Create: `docs/local-acceptance.md`

- [x] **Step 1: Write the acceptance runbook with exact non-destructive checks**

Create `docs/local-acceptance.md` with these commands and expected facts:

```markdown
# Local Acceptance

## 1. Baseline

Run `bash migrate-cleanup.sh > /tmp/mb-lite-dry-run.txt`.
Confirm `git -C /Users/wangxiaoguang/IdeaProjects/onduty status --short` is unchanged.
Confirm the dry-run lists central registry creation, old prompt blocks, old hooks,
old agents/commands, and registered project adapters.

## 2. Human Apply Gate

Do not run `--apply` until the user has reviewed `/tmp/mb-lite-dry-run.txt` and
explicitly approved the destructive cleanup.

## 3. Apply

Run `bash migrate-cleanup.sh --apply` and record the printed transaction ID.
Run `bash install.sh`.

## 4. Filesystem Checks

Run `readlink ~/.agents/skills/mb` and expect the memory-bank-lite repository.
Run `readlink ~/.claude/skills/mb` and expect `~/.agents/skills/mb`.
Confirm `~/.codex/skills/mb` and `~/.config/opencode/skills/mb` do not exist.
Search global and onduty AGENTS/CLAUDE files for `memory-bank` markers and expect
no matches.

## 5. Host Checks

In Codex, run `$mb start` from onduty.
In Claude Code, run `/mb start` from onduty.
In OpenCode, run `/mb start` from onduty.
Each host must report the same Bank path and at most 8192 context bytes.

## 6. Confirmed Write Check

Run `done` in one host, review the draft, and decline confirmation. Record SHA-256
for status.md, checklist.md, and progress.md before and after; all must match.
Run `done` again and confirm. Run `start` in another host and verify the new
progress summary appears.

## 7. Rollback Drill

Run `bash migrate-cleanup.sh --rollback TRANSACTION_ID` only if acceptance fails.
Rollback restores configuration and registration files but leaves Bank content.
```

- [x] **Step 2: Verify the runbook contains every approved acceptance criterion**

Run:

```bash
rg -n "dry-run|explicitly approved|Codex|Claude Code|OpenCode|8192|decline|confirm|rollback" docs/local-acceptance.md
```

Expected: each term appears in the matching numbered section.

- [x] **Step 3: Commit the acceptance runbook**

```bash
git add docs/local-acceptance.md
git commit -m "docs: add local memory acceptance runbook"
```

- [x] **Step 4: Execute dry-run only and pause for approval**

Run: `bash migrate-cleanup.sh > /tmp/mb-lite-dry-run.txt`

Expected: exit `0`, no changed user configuration, and a complete action list.
Present `/tmp/mb-lite-dry-run.txt` to the user. Do not run real `--apply` in the
same step.

## Milestone Verification

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
bash -n install.sh uninstall.sh migrate-cleanup.sh
git status --short
```

Expected:

- all tests pass;
- Python compilation and Bash syntax checks exit `0`;
- isolated-HOME install creates no AGENTS/CLAUDE content, hooks, agents,
  plugins, project adapters, Codex prompt, or per-host skill copy;
- dry-run is byte-identical;
- rollback restores every backed-up configuration file by SHA-256;
- the working tree is clean before the real-machine dry-run.
