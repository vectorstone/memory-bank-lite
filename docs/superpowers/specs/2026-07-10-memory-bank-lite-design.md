# Memory Bank Lite Design

**Date:** 2026-07-10
**Status:** Approved
**Repository:** `/Users/wangxiaoguang/IdeaProjects/memory-bank-lite`

## 1. Goal

Build a source-installed project memory skill whose only responsibility is to
persist, restore, and search concise project knowledge across tasks and chat
sessions.

The tool must:

- isolate memory by Git repository;
- share the same memory across clones and worktrees of that repository;
- store every Bank under
  `/Users/wangxiaoguang/Docs/obsidian/memory-bank/projects/`;
- support Codex, Claude Code, and OpenCode;
- load no Memory Bank content unless the user explicitly invokes it;
- save nothing unless the user explicitly invokes `done` and approves the
  generated draft;
- preserve the existing `.memory-bank` Markdown structure and existing data;
- keep the default `start` output at or below 8 KB.

## 2. Product Boundary

Memory Bank Lite provides exactly four actions:

- `init` - register a repository and initialize a compatible Bank;
- `start` - restore compact recent project context;
- `done` - draft, confirm, and save a session summary;
- `recall` - search historical project memory on demand.

The following features are explicitly out of scope:

- global or project `AGENTS.md` / `CLAUDE.md` instructions;
- automatic SessionStart, Stop, SessionEnd, or PreCompact behavior;
- hooks, plugins, agents, or subagents;
- automatic capture when `done` was not invoked;
- TDD, SOLID, Clean Architecture, protected-path, or other engineering rules;
- plan creation/execution, SDD, review pipelines, judges, code graphs,
  embeddings, and semantic indexing as active workflows;
- pipx, PyPI, Homebrew, release, or backward-compatible package maintenance;
- project-level `.codex/`, `.opencode/`, or `.claude/` adapters.

Existing plans, notes, roadmap, research, and other Bank files remain readable
historical data. Their presence does not activate the removed features.

## 3. Repository Layout

```text
memory-bank-lite/
|-- SKILL.md
|-- scripts/
|   `-- mb.py
|-- templates/
|   `-- .memory-bank/
|-- tests/
|-- install.sh
|-- uninstall.sh
|-- migrate-cleanup.sh
`-- docs/superpowers/specs/
```

`mb.py` is a single Python standard-library CLI. Runtime installation does not
require a Python package environment or any third-party dependency.

## 4. Skill And Host Entry Points

The canonical skill name is `mb`.

```text
~/.agents/skills/mb
    -> /Users/wangxiaoguang/IdeaProjects/memory-bank-lite

~/.claude/skills/mb
    -> ~/.agents/skills/mb
```

Codex and OpenCode discover the canonical skill through `~/.agents/skills`.
Claude Code receives only the compatibility symlink above. The installer must
not create `~/.codex/skills/mb` or `~/.config/opencode/skills/mb`.

Host invocation is:

```text
Claude Code: /mb <action>
OpenCode:    /mb <action>
Codex:       $mb <action>
```

Claude Code and OpenCode may receive a host-native `mb` command wrapper, but it
must be no more than ten lines and may only load the `mb` skill with the supplied
arguments. If the installed OpenCode version exposes the skill directly as
`/mb` through `slash: true`, its wrapper is not installed.

Codex receives no custom prompt. Codex custom prompts are deprecated and are
invoked under `/prompts:<name>`, so they cannot provide a stable top-level
`/mb`. Codex uses explicit skill invocation instead.

References:

- Codex custom prompts:
  <https://learn.chatgpt.com/docs/custom-prompts.md>
- OpenCode custom commands:
  <https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/commands.mdx>

## 5. Central Storage And Registry

The only registry is:

```text
/Users/wangxiaoguang/Docs/obsidian/memory-bank/registry.json
```

All Banks live under:

```text
/Users/wangxiaoguang/Docs/obsidian/memory-bank/projects/
```

Registry schema:

```json
{
  "version": 1,
  "projects_root": "/Users/wangxiaoguang/Docs/obsidian/memory-bank/projects",
  "projects": {
    "github.com/example/onduty": {
      "project_id": "onduty-122d9ee9bd04",
      "remote": "github.com/example/onduty",
      "bank_path": "/Users/wangxiaoguang/Docs/obsidian/memory-bank/projects/onduty-122d9ee9bd04/.memory-bank",
      "known_roots": [
        "/Users/wangxiaoguang/IdeaProjects/onduty"
      ]
    }
  }
}
```

There are no per-host registries or per-host Bank pools after migration.

## 6. Repository Identity

Git remote selection order is:

1. `remote.pushDefault`;
2. `origin`;
3. the only configured remote;
4. fail on multiple remotes without an unambiguous selection;
5. use the canonical absolute repository path when no remote exists.

Remote normalization converts equivalent SSH and HTTPS forms to one key. For
example:

```text
git@github.com:example/onduty.git
ssh://git@github.com/example/onduty.git
https://github.com/example/onduty.git
```

become:

```text
github.com/example/onduty
```

Normalization lowercases the host, removes credentials, query parameters,
fragments, a trailing slash, and a trailing `.git`. Repository path casing is
preserved.

The project ID is `<repository-slug>-<12-character-hash>`, where the hash is
derived from the normalized repository key. Clones and worktrees of the same
remote therefore share one Bank.

`known_roots` is diagnostic metadata written by `init`. It does not participate
in identity resolution, and `start` must not update it.

Changing a repository remote creates a new identity. Reusing an old Bank
requires explicit `init --adopt <old-repository-key>` confirmation.

## 7. Compatible Bank Structure

`init` preserves the existing Markdown-oriented structure. At minimum, a valid
Bank contains readable:

```text
status.md
checklist.md
progress.md
```

The compatible template may also create:

```text
roadmap.md
research.md
backlog.md
lessons.md
notes/
plans/
```

Only the three required files participate in active-state validation. Optional
files are read only when an action needs them.

### 7.1 Compatible Write Protocol

Lite-owned current state is stored in a bounded block near the top of
`status.md`:

```markdown
<!-- mb-lite:current:start -->
## Memory Bank Lite Current

**Focus:** Implement the lightweight memory core
**Next:** Add central registry contract tests
**Blockers:** None
<!-- mb-lite:current:end -->
```

The first confirmed `done` inserts the block after the document's first H1.
Later writes replace only the paired block. All legacy status content remains
byte-identical outside it.

Completed checklist items are exact matches against unchecked Markdown list
items. A missing or ambiguous match fails the whole apply. New items are added
under a paired `mb-lite:open-items` block; existing checklist sections remain
untouched.

Every new progress entry has a stable marker and fixed headings:

```markdown
<!-- mb-lite-entry:20260710T120000Z-a1b2c3d4 -->
### 2026-07-10 12:00 - Defined the registry contract
```

Legacy progress content remains valid. When fewer than three Lite entries
exist, `start` fills the remaining recent slots from the newest legacy `###`
entries without rewriting them.

## 8. Action Flows

### 8.1 `init`

1. Resolve and normalize repository identity.
2. Derive the project ID and Bank path.
3. Refuse an ambiguous remote selection.
4. Create missing compatible Bank files without overwriting existing data.
5. Atomically register the project in the central registry.
6. Report the repository key, project ID, and Bank path.

Initialization is idempotent. It never creates or modifies host instructions,
hooks, agents, or project adapters.

### 8.2 `start`

`start` is strictly read-only and returns no more than 8 KB, approximately 2,000
tokens.

It returns:

- current state, focus, and blockers from `status.md`;
- unchecked items from `checklist.md`;
- only the current stage of an active plan;
- the newest three explicit `done` entries from `progress.md`;
- at most five note titles matching current-stage keywords.

Note matching is deterministic: case-fold the current focus and stage, split
on Unicode non-alphanumeric boundaries, discard terms shorter than two
characters, and include titles containing at least one remaining term. No
matching terms means no note titles are included.

It does not load full notes, plans, roadmap history, or research. Those bodies
are retrieved through `recall`.

When output exceeds 8 KB, trimming order is:

1. note titles;
2. the oldest included progress entry;
3. active-plan supporting detail.

Current focus, blockers, unchecked items, and next steps are never trimmed.
The output states when trimming occurred.

If those protected sections alone exceed 8 KB, `start` fails with section byte
counts and asks the caller to use `start --section <name>` or `recall`. It never
silently exceeds the cap or truncates a protected section.

`start` does not repair data, create indexes, update timestamps, update
`known_roots`, or write temporary files into the Bank.

### 8.3 `done`

`done` is a two-phase operation.

Phase 1 creates an in-conversation draft containing:

- summary of changes;
- decisions;
- next steps;
- blockers;
- key files;
- completed checklist items;
- new checklist items;
- an optional reusable note.

The draft also includes a Bank revision computed from the SHA-256 content of
`status.md`, `checklist.md`, and `progress.md`. The draft is not written to
disk.

Phase 2 begins only after explicit user confirmation. The agent submits the
structured payload to `mb.py done --apply`. The CLI obtains an exclusive lock,
rechecks the revision, atomically updates `status.md` and `checklist.md`, and
appends one entry to `progress.md`. A note is created only when the confirmed
payload contains reusable knowledge.

If the user does not confirm, the command writes nothing. Forgetting to invoke
or confirm `done` means the session is not persisted; this is intentional.

### 8.4 `recall`

`recall` performs local lexical search over:

- `progress.md`;
- `notes/`;
- active and completed `plans/`;
- decision entries in compatible files.

Default output is a bounded compact result list. `--expand <id>` returns one
selected body. Missing optional directories degrade to fewer results, not an
error. Recall does not build an index or write any file.

Result IDs are deterministic 12-character hashes of repository-relative Bank
path, heading, and source offset. Default output returns at most ten results
with bounded snippets.

## 9. Write Safety And Failure Semantics

A Bank is active only when:

- its repository key exists in the central registry;
- its `bank_path` is contained by `projects_root`;
- the Bank directory exists;
- `status.md`, `checklist.md`, and `progress.md` are readable.

The resolver must never treat a fallback string as an active Bank.

Registry and Bank writes use an exclusive lock, a temporary file in the target
directory, `fsync`, and atomic replacement. A changed Bank revision causes
`done --apply` to fail without writing so the draft can be regenerated.

The done payload is JSON with fixed field types. File paths must resolve inside
the current repository. Payload text is data and is never evaluated as shell.

Exit codes are:

```text
0  success
1  invalid arguments or payload
2  project is not initialized
3  Bank revision conflict
4  corrupt registry or Bank
5  lock acquisition failure
```

Commands produce concise Markdown by default and support `--json` for stable
agent parsing.

## 10. Migration And Cleanup

`migrate-cleanup.sh` is dry-run by default. `--apply` performs the approved
changes.

Migration order is:

1. Read legacy Codex, Claude Code, and OpenCode registries.
2. Convert their records to normalized repository keys.
3. Verify required Bank files.
4. Merge identical records.
5. Stop when the same repository key points to different Bank content.
6. Write the central registry.
7. Preserve all Bank data in the shared projects directory.
8. Remove old Memory Bank host integration.
9. Install the new skill symlinks and thin supported command wrappers.
10. Verify all three hosts resolve the same Bank.

Global cleanup removes only Memory Bank-managed content:

- marked sections from global `AGENTS.md` and `CLAUDE.md` files;
- hooks marked `[memory-bank-skill]` from Claude settings;
- old registered Memory Bank agents, commands, hooks, and skill symlinks;
- legacy host registries after successful central-registry migration.

An installed file is considered Memory Bank-managed only when at least one
strong ownership signal exists: a paired Memory Bank marker, an install
manifest entry, a Memory Bank hook marker, or byte identity with a known legacy
artifact. A filename match by itself is never enough to delete a file.

Project cleanup scans projects found in legacy registries and manifests. It
removes only managed AGENTS sections, manifest-owned `.codex/` and `.opencode/`
files, the Memory Bank OpenCode plugin reference, and Memory Bank-installed Git
hooks. Unknown and user-owned content is preserved.

Before applying changes, the script backs up every modified configuration file
under:

```text
~/.local/state/mb-migration/<timestamp>/
```

The backup manifest records original path, backup path, SHA-256, cleanup action,
and central registry path. `--rollback <timestamp>` restores configuration and
registration files but never rolls back later Bank content.

The new `uninstall.sh` removes only Lite skill symlinks and thin command
wrappers. It never removes the central registry or any Bank data.

## 11. Testing Strategy

Runtime code uses only the Python standard library. Tests use `unittest` plus
shell smoke tests.

Required automated coverage includes:

- remote normalization, selection precedence, worktree/clone identity, and
  path fallback;
- registry idempotency, corruption refusal, containment, atomic writes, and
  concurrent registration;
- start selection, 8 KB cap, trimming priority, and byte-identical read-only
  behavior;
- done confirmation boundary, revision conflicts, concurrent apply, append-only
  progress, conditional notes, path validation, and payload non-execution;
- recall result limits, single-result expansion, missing optional data, and
  read-only behavior;
- migration dry-run, marker-safe cleanup, user-content preservation, Bank
  conflict refusal, plugin-reference cleanup, and rollback byte identity;
- source symlink installation and absence of hooks, agents, plugins, project
  adapters, and Memory Bank prompt injection.

## 12. Local Acceptance Criteria

Using `onduty` as the real project:

1. Migration dry-run reports every planned cleanup without modifying files.
2. Apply removes old Memory Bank prompt blocks, hooks, agents, and adapters.
3. Existing Bank files remain intact.
4. Codex can run `$mb start`.
5. Claude Code can run `/mb start`.
6. OpenCode can run `/mb start`.
7. All three hosts resolve the same central Bank path.
8. Start output is at most 8 KB.
9. An unconfirmed `done` draft leaves the Bank byte-identical.
10. A confirmed `done` appears in `start` from another host.
11. No global or project AGENTS/CLAUDE file contains Memory Bank content.
12. Outside explicit `init`, `start`, `done`, and `recall` invocations, Memory
    Bank Lite performs no action.

## 13. Final Design Decision

Memory Bank Lite is a new personal source-installed repository, not a compatible
release of `skill-memory-bank`. It preserves existing Bank data and Markdown
formats while rejecting the old product's always-on prompt injection,
automation, engineering governance, and multi-agent toolkit.

The durable contract is intentionally small: explicit invocation, central
project-isolated storage, compact restoration, confirmed persistence, and
read-only recall.
