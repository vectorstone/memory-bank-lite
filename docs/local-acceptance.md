# Local Acceptance

## 1. Baseline

Capture file content, type, mode, and symlink targets before and after the
dry-run. It also discovers existing project roots from legacy registries and
the Git repositories behind known legacy skill symlinks. The snapshot excludes
only `.git` inside project/source roots; it includes tracked and untracked
working-tree content and records, but does not recursively follow, other
symlinks. Run the block in one shell so the snapshot function is available for
both captures:

```bash
mb_lite_snapshot() {
  python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import stat

home = Path.home()
legacy_registries = (
    home / ".codex/memory-bank/registry.json",
    home / ".claude/memory-bank/registry.json",
    home / ".config/opencode/memory-bank/registry.json",
)
legacy_skills = (
    home / ".claude/skills/skill-memory-bank",
    home / ".claude/skills/memory-bank",
    home / ".codex/skills/memory-bank",
    home / ".config/opencode/skills/memory-bank",
    home / ".cursor/skills/memory-bank",
    home / ".pi/agent/skills/memory-bank",
)
roots = [
    ("codex-config", home / ".codex/AGENTS.md", False),
    ("codex-rules", home / ".codex/RULES.md", False),
    ("codex-hooks", home / ".codex/hooks.json", False),
    ("codex-settings", home / ".codex/settings.json", False),
    ("codex-prompts", home / ".codex/prompts", False),
    ("codex-skills", home / ".codex/skills", False),
    ("codex-registry", home / ".codex/memory-bank", False),
    ("claude-config", home / ".claude/CLAUDE.md", False),
    ("claude-agents-config", home / ".claude/AGENTS.md", False),
    ("claude-rules", home / ".claude/RULES.md", False),
    ("claude-hooks-config", home / ".claude/hooks.json", False),
    ("claude-settings", home / ".claude/settings.json", False),
    ("claude-agents", home / ".claude/agents", False),
    ("claude-commands", home / ".claude/commands", False),
    ("claude-hooks", home / ".claude/hooks", False),
    ("claude-skills", home / ".claude/skills", False),
    ("claude-registry", home / ".claude/memory-bank", False),
    ("claude-mb-config", home / ".claude/memory-bank-config.json", False),
    ("opencode", home / ".config/opencode", False),
    ("cursor-agents", home / ".cursor/AGENTS.md", False),
    ("cursor-rules", home / ".cursor/RULES.md", False),
    ("cursor-hooks", home / ".cursor/hooks.json", False),
    ("cursor-settings", home / ".cursor/settings.json", False),
    ("cursor-manifest", home / ".cursor/.mb-manifest.json", False),
    ("cursor-commands", home / ".cursor/commands", False),
    ("cursor-skills", home / ".cursor/skills", False),
    ("cursor-registry", home / ".cursor/memory-bank", False),
    ("pi-agents", home / ".pi/agent/AGENTS.md", False),
    ("pi-rules", home / ".pi/agent/RULES.md", False),
    ("pi-hooks", home / ".pi/agent/hooks.json", False),
    ("pi-settings", home / ".pi/agent/settings.json", False),
    ("pi-prompts", home / ".pi/agent/prompts", False),
    ("pi-skills", home / ".pi/agent/skills", False),
    ("pi-registry", home / ".pi/agent/memory-bank", False),
    (
        "central-memory",
        Path("/Users/wangxiaoguang/Docs/obsidian/memory-bank"),
        False,
    ),
    ("onduty", Path("/Users/wangxiaoguang/IdeaProjects/onduty"), True),
]


def git_root(path: Path) -> Path:
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.exists() or marker.is_symlink():
            return candidate
    return path


discovered: dict[tuple[str, str], Path] = {}
for registry in legacy_registries:
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        continue
    projects = data.get("projects", {}) if isinstance(data, dict) else {}
    if not isinstance(projects, dict):
        continue
    for raw_root in projects:
        if not isinstance(raw_root, str) or not raw_root:
            continue
        try:
            project = Path(raw_root).expanduser()
            if not project.is_absolute() or not project.is_dir():
                continue
            project = project.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        discovered[("legacy-project", str(project))] = project

for skill in legacy_skills:
    if not skill.is_symlink():
        continue
    try:
        target = skill.resolve(strict=True)
        if not target.is_dir():
            continue
        source = git_root(target)
    except (OSError, RuntimeError):
        continue
    discovered[("legacy-skill-source", str(source))] = source

for (kind, raw_path), path in sorted(discovered.items()):
    suffix = hashlib.sha256(raw_path.encode("utf-8")).hexdigest()[:12]
    roots.append((f"{kind}-{suffix}", path, True))


def emit(label: str, relative: str, kind: str, mode: int, value: str) -> None:
    print(
        json.dumps(
            {
                "kind": kind,
                "label": label,
                "mode": f"{mode:04o}",
                "path": relative,
                "value": value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def visit(label: str, root: Path, path: Path, exclude_git: bool) -> None:
    relative = "." if path == root else path.relative_to(root).as_posix()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        emit(label, relative, "missing", 0, "")
        return
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        emit(label, relative, "symlink", mode, os.readlink(path))
        return
    if stat.S_ISREG(metadata.st_mode):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        emit(label, relative, "file", mode, f"{metadata.st_size}:{digest}")
        return
    if stat.S_ISDIR(metadata.st_mode):
        emit(label, relative, "directory", mode, "")
        for child in sorted(path.iterdir(), key=lambda value: value.name):
            if exclude_git and path == root and child.name == ".git":
                continue
            visit(label, root, child, exclude_git)
        return
    emit(label, relative, "special", mode, str(metadata.st_rdev))


for label, root, exclude_git in roots:
    emit(label, "@root", "root", 0, str(root))
    visit(label, root, root, exclude_git)
PY
}

mb_lite_snapshot > /tmp/mb-lite-fs-before.jsonl
git -C /Users/wangxiaoguang/IdeaProjects/onduty status --short > /tmp/mb-lite-onduty-before.txt
bash migrate-cleanup.sh > /tmp/mb-lite-dry-run.txt
mb_lite_snapshot > /tmp/mb-lite-fs-after.jsonl
git -C /Users/wangxiaoguang/IdeaProjects/onduty status --short > /tmp/mb-lite-onduty-after.txt
cmp /tmp/mb-lite-fs-before.jsonl /tmp/mb-lite-fs-after.jsonl
cmp /tmp/mb-lite-onduty-before.txt /tmp/mb-lite-onduty-after.txt
shasum -a 256 /tmp/mb-lite-fs-before.jsonl /tmp/mb-lite-fs-after.jsonl
```

Review `/tmp/mb-lite-dry-run.txt`. As applicable to the current machine, the
dry-run must list central registry creation or merge, old prompt blocks, old
hooks, old agents and commands, and registered project adapters. It must not
modify user configuration, project configuration, registrations, or Bank
content. Both `cmp` commands must exit zero; matching snapshot hashes provide a
compact audit record.

## 2. Human Apply Gate

Never run `--apply` until the user has reviewed
`/tmp/mb-lite-dry-run.txt` and explicitly approved the destructive cleanup.
Review or test approval is not apply approval.

## 3. Apply (Future Only)

Run these commands only after the Human Apply Gate is satisfied:

```bash
bash migrate-cleanup.sh --apply
# Record the transaction ID printed by the successful apply.
bash install.sh
```

Do not continue to acceptance checks unless both commands succeed and the
transaction ID has been recorded.

## 4. Filesystem Checks

Verify the canonical source links:

```bash
readlink ~/.agents/skills/mb
readlink ~/.claude/skills/mb
```

`~/.agents/skills/mb` must resolve to the Memory Bank Lite source repository.
`~/.claude/skills/mb` must point to the canonical agents link at
`~/.agents/skills/mb`.

Verify that Codex and OpenCode do not contain duplicate skill installations:

```bash
test ! -e ~/.codex/skills/mb && test ! -L ~/.codex/skills/mb
test ! -e ~/.config/opencode/skills/mb && test ! -L ~/.config/opencode/skills/mb
```

Verify that no managed Memory Bank prompt blocks remain in global or `onduty`
`AGENTS.md` or `CLAUDE.md` files after apply:

```bash
rg -n "memory-bank-(codex|claude|opencode|global|project):(start|end)|memory-bank:(start|end)" \
  ~/.codex/AGENTS.md ~/.claude/CLAUDE.md ~/.config/opencode/AGENTS.md \
  /Users/wangxiaoguang/IdeaProjects/onduty/AGENTS.md \
  /Users/wangxiaoguang/IdeaProjects/onduty/CLAUDE.md
```

No matches are expected. Missing optional files may be omitted from the
command without failing acceptance.

## 5. Host Checks

From `/Users/wangxiaoguang/IdeaProjects/onduty`, invoke the native command in
each host:

- Codex: `$mb start`
- Claude Code: `/mb start`
- OpenCode: `/mb start`

All three hosts must report the same Bank path. Each returned context must be
no more than 8192 bytes.

## 6. Confirmed Write Check

Set `BANK_PATH` to the common Bank path reported by the host checks. Record the
SHA-256 values before drafting a write:

```bash
printf 'Bank path reported by start: '
IFS= read -r BANK_PATH
shasum -a 256 "$BANK_PATH/status.md" "$BANK_PATH/checklist.md" "$BANK_PATH/progress.md" \
  > /tmp/mb-lite-bank-before.sha256
```

Run `done` in one host, review the draft, and decline confirmation. Then verify
that all three files are unchanged:

```bash
shasum -a 256 "$BANK_PATH/status.md" "$BANK_PATH/checklist.md" "$BANK_PATH/progress.md" \
  > /tmp/mb-lite-bank-declined.sha256
cmp /tmp/mb-lite-bank-before.sha256 /tmp/mb-lite-bank-declined.sha256
```

Run `done` again, review the draft, and confirm the write. Run `start` in
another host and verify that it shows the new progress summary.

## 7. Rollback Drill

Run rollback only when acceptance fails, using the transaction ID recorded
during apply:

```bash
bash migrate-cleanup.sh --rollback TRANSACTION_ID
```

Rollback must restore configuration and registration files. It must preserve
the Bank and all Bank content.
