# Memory Bank Lite v2 Design

**Date:** 2026-07-19
**Status:** Approved through user interview and implemented
**Repository:** `/Users/wangxiaoguang/IdeaProjects/memory-bank-lite`

## 1. Outcome

Memory Bank Lite v2 remains an explicitly invoked project-memory tool while
adding three capabilities:

1. portable Project and Workspace Banks shared by Codex, Claude Code, and
   OpenCode;
2. explicit, reviewed promotion of stable project knowledge into a compatible
   `claude-obsidian` vault;
3. first-class attention points, health checks, and safe multi-Bank closeout.

The product has two memory layers with different ownership:

- **Operational memory:** Project and Workspace Banks under `memory-bank/` in
  the configured Obsidian vault. This is the source of truth for current focus,
  next steps, blockers, recent progress, and active attention points.
- **Long-term knowledge:** managed sections in Project and Workspace knowledge
  pages under `wiki/sources/`. These contain stable decisions, architecture,
  invariants, milestones, lessons, and superseded knowledge.

Knowledge moves from the first layer to the second only through an explicit
`/mb publish` followed by a second confirmation.

## 2. Non-Negotiable Product Boundaries

- No action runs implicitly.
- No SessionStart, Stop, SessionEnd, PreCompact, Git, launchd, or scheduled
  hooks are installed.
- No Bank is read until the user invokes an `mb` action.
- No Bank is written without `/mb done`, `/mb init`, an explicit workspace
  mutation, migration apply, or a confirmed repair action.
- No wiki knowledge is written without `/mb publish` and a second explicit
  confirmation.
- `/mb publish` never supports fast-write mode.
- `/mb done --yes` remains a one-shot authorization and may update all affected
  Banks in a Workspace transaction.
- The tool does not run `git add`, `commit`, `pull`, or `push` in the vault.
- Markdown remains the durable source of truth. v2 does not require SQLite,
  embeddings, a vector store, or a third-party Python package.
- Project repositories and Workspace roots receive no files, hooks, or
  instruction blocks.

## 3. Adopted And Rejected Ideas

### 3.1 From `skill-memory-bank`

Keep:

- explicit `init`, `start`, `done`, and `recall` actions;
- portable host adapters around one shared skill;
- Markdown Banks, managed markers, revision checks, and bounded start output;
- fail-closed filesystem containment and idempotent installation.

Do not restore:

- automatic session hooks;
- engineering-rule enforcement, plans, reviewers, subagents, pipelines, code
  graphs, package distribution, or project adapters.

### 3.2 From `agent-memory-vault`

Adopt in lightweight form:

- Markdown as the only durable fact source;
- separate portable identity data from device-local path bindings;
- Project and Workspace scope;
- a global local-process lock plus per-document revision checks;
- structured current, superseded, and review-due knowledge;
- `verified_at` and `review_after_days` semantics for attention points;
- an explicit, read-only Doctor.

Do not adopt in v2:

- Stop hooks, automatic closeout, periodic audit, notifications, launchd;
- session claims, Git baseline ownership, automatic commits;
- SQLite/FTS, Zvec, embedding models, candidate promotion, or Agent evolution.

Session claims are unnecessary while Memory Bank Lite does not own Git commits:
local locking, expected revisions, content hashes, and journaled transactions
cover its write boundary.

### 3.3 From `claude-obsidian`

Adopt:

- the target-vault contract (`WIKI.md`, `wiki/index.md`, `wiki/log.md`,
  `wiki/hot.md`, `wiki/sources/_index.md`, `.vault-meta/transport.json`);
- the current methodology-mode router for source paths;
- per-file `wiki-lock.sh` locks in sorted path order;
- transport selection and fallback declared by the target vault;
- source-page frontmatter, wikilinks, index/log/hot updates, and duplicate
  avoidance.

Do not invoke another Agent skill as a runtime API. The `mb` skill owns a narrow,
versioned `claude-obsidian` adapter so behavior remains consistent across hosts.

## 4. Portable And Local Storage

### 4.1 Vault Layout

```text
<vault>/
|-- memory-bank/
|   |-- catalog.json
|   |-- projects/
|   |   `-- <project-id>/.memory-bank/
|   |-- workspaces/
|   |   `-- <workspace-id>/.memory-bank/
|   |-- closeout-state/
|   |   `-- <workspace-id>.json
|   `-- publish-state/
|       `-- <scope-id>.json
|-- wiki/
|   |-- sources/
|   |   |-- <project>-project-knowledge.md
|   |   `-- <workspace>-workspace-knowledge.md
|   |-- index.md
|   |-- log.md
|   `-- hot.md
`-- .vault-meta/
    `-- transport.json
```

All paths in `catalog.json`, closeout-state files, and publish-state files are
vault-relative. No device absolute path is portable data. Closeout state stores
only per-member Git cursors and the last successful compound transaction ID; it
does not participate in identity resolution.

### 4.2 Device Configuration

Default paths:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/memory-bank-lite/config.json
${XDG_STATE_HOME:-$HOME/.local/state}/memory-bank-lite/bindings.json
${XDG_STATE_HOME:-$HOME/.local/state}/memory-bank-lite/transactions/
```

On macOS the same XDG-compatible paths are used to keep the installation
identical to Linux.

Configuration precedence for the vault root is:

1. `MB_LITE_VAULT_PATH` for the current process;
2. `vault_path` in `config.json`;
3. fail with an actionable configuration command.

`config.json` stores durable device settings. `bindings.json` maps local
canonical roots to portable scope IDs and may record multiple worktrees for one
Project. Transaction journals and backups are local state with mode `0700` and
never enter the vault.

### 4.3 Configuration Actions

```text
/mb config set-vault <path>
install.sh --vault-path <path>
```

Both validate the target but do not initialize a Bank or mutate wiki content.
The tool never scans the device for a likely vault.

## 5. Identity Model

### 5.1 Project Identity

The stable internal key is `project_id`.

When a selected Git remote exists:

```text
normalized repository key -> slug + SHA-256 prefix -> project_id
```

Equivalent SSH and HTTPS remote forms normalize to the same repository key.
Credentials, query strings, fragments, default ports, trailing slashes, and a
trailing `.git` are removed.

When no remote exists, explicit `init` creates a stable local Project ID. The
absolute repository root is only a local binding. Another device must use an
explicit adopt operation after reviewing the existing Project and Bank.

Adding, removing, or changing a remote never silently creates or merges a
Project. The user confirms whether the new repository key becomes an alias of
the existing Project ID.

### 5.2 Workspace Identity

A Workspace is a first-class scope with a generated stable `workspace_id`.
Its identity does not depend on its root path or the current member set.

Workspace initialization is explicit:

```text
/mb init --workspace --name <name> [--member <path> ...]
/mb init --workspace --name <name> --convert-project [--member <path> ...]
```

The tool may discover immediate child Git roots, but only presents candidates.
No member is registered until confirmation. Later membership changes require
explicit `workspace add` or `workspace remove` actions.

When the exact Workspace root is already bound to a Project, conversion is an
explicit preview-and-confirm operation. The preview identifies the source
Project and Bank. Confirmed apply copies that Bank into the new Workspace,
registers the reviewed members, and conditionally replaces only the current
root Binding. The source Project record and Bank remain intact because the same
Project identity can be shared by other clones, worktrees, devices, or
Workspaces. Conversion is journaled; failure restores Catalog and Binding bytes
and removes every scope directory created by that attempt.

Members reference unique Project IDs. Multiple worktrees of the same remote are
one Project member, while local bindings retain each checkout path, branch, and
current commit for evidence collection.

### 5.3 Scope Resolution

Resolution order is:

1. an explicit scope ID or explicit `--workspace` action;
2. an exact local Workspace-root binding;
3. the enclosing Git repository and its normalized repository key;
4. a local no-remote Project binding;
5. fail without reading or writing a Bank.

Running from an exact registered Workspace root selects the Workspace. Running
inside a member worktree selects that Project unless the user explicitly names
the Workspace.

## 6. Portable Catalog

`memory-bank/catalog.json` has a strict versioned JSON schema. A conceptual v2
shape is:

```json
{
  "schema_version": 2,
  "projects": {
    "service-a-12ab34cd56ef": {
      "display_name": "service-a",
      "repository_keys": ["github.com/example/service-a"],
      "bank_path": "memory-bank/projects/service-a-12ab34cd56ef/.memory-bank"
    }
  },
  "workspaces": {
    "order-sync-a1b2c3d4e5f6": {
      "display_name": "Order sync",
      "members": ["service-a-12ab34cd56ef"],
      "bank_path": "memory-bank/workspaces/order-sync-a1b2c3d4e5f6/.memory-bank"
    }
  }
}
```

Invariants:

- all IDs are unique across their type;
- all paths are normalized, vault-relative, and contained by `memory-bank/`;
- repository keys may alias one Project but may not resolve to multiple Projects;
- Workspace members must exist;
- unknown schema versions fail closed;
- invalid unrelated entries prevent catalog mutation rather than being dropped.

## 7. Bank Schema

### 7.1 Files

Required v2 files:

```text
status.md
checklist.md
progress.md
attention.md
```

Compatible optional files remain:

```text
roadmap.md
research.md
backlog.md
lessons.md
notes/
plans/
```

Existing v1 content outside managed markers remains byte-identical.

### 7.2 Attention Entries

Each entry has a stable scope-local ID and these fields:

```text
id, title, scope, severity, status, reason, evidence,
verified_at, review_after_days
```

Allowed statuses are `active`, `resolved`, and `superseded`. Review expiry marks
an entry as due; it does not delete, resolve, or reactivate it. New IDs are
allocated while holding the Bank or compound transaction lock.

`/mb done` payloads can add, update, resolve, or supersede entries. The CLI
validates references to existing IDs before any file is staged.

### 7.3 Bank Revision

The v2 revision is a stable tree digest of every regular Bank Markdown file,
excluding locks, temporary files, and local transaction artifacts. This detects
manual edits to notes or attention content that the current three-core-file
revision misses.

## 8. Command Semantics

### 8.1 `init`

- Project init resolves or creates a Project identity, initializes missing v2
  Bank files, and adds the local binding.
- Workspace init drafts the candidate member set and requires confirmation.
- Adopt always displays the target ID, Bank, and identity evidence before apply.
- Init never edits project/workspace roots.

### 8.2 `start`

Project start returns current state, active attention, open items, active plan
stage, recent progress, and matching note titles.

Workspace start adds a bounded one-line summary for each relevant member. It is
read-only and does not rediscover or rewrite membership.

The default output remains at or below 8 KiB. Trimming removes note titles,
older progress, stage detail, lower-severity attention, and inactive member
summaries in that order. The output states how many entries were omitted.

### 8.3 `done`

Normal mode is draft -> display -> confirmation -> apply. `done --yes` skips the
display and confirmation but never bypasses validation.

Project done updates one Bank. Workspace done creates a compound payload for the
Workspace Bank and every affected Project Bank. Affected Projects are derived
from current-session tool/file evidence, member Git status, commits since the
last Workspace closeout baseline, and user corrections in normal-mode preview.

Uncertain membership or affected-scope detection fails closed in `--yes` mode.

Compound apply is all-or-nothing:

1. validate identities, payloads, sensitive-content policy, and all expected
   revisions;
2. acquire the local global lock and relevant Bank locks in stable ID order;
3. write and fsync a recovery journal before target mutation;
4. stage and validate every output;
5. replace targets and verify final hashes;
6. roll back all applied outputs on any ordinary failure;
7. retain the journal and block future writes if crash recovery is uncertain.

### 8.4 `publish`

Publish has four logical phases:

1. **Snapshot:** collect the current scope, Bank revisions, current knowledge
   pages, publish state, Git commits/diffs since the last cursor, and a bounded
   set of referenced key files.
2. **Synthesis:** the active Agent produces stable knowledge deltas and excludes
   current tasks, next steps, blockers, and ordinary recent progress.
3. **Preview:** the CLI validates the payload, resolves target pages, performs
   duplicate/conflict checks, renders exact outputs, and returns file diffs.
4. **Apply:** after explicit confirmation, the exact payload is reapplied only if
   every expected revision and content hash still matches.

Project publish updates one Project knowledge page. Workspace publish may update
one Workspace page and every affected Project page in one transaction.

Project-local knowledge appears only on the Project page. Cross-repository
contracts and decisions appear only on the Workspace page. Pages cross-link
instead of duplicating content.

Publish never scans the entire repository by default. It reads the Bank, Git
summary since the cursor, and only key files needed to verify candidate claims.
An invalid Git cursor, non-ancestor commit, rebase ambiguity, or content conflict
requires a new baseline decision.

### 8.5 `recall`

Default recall searches:

- the current Project or Workspace Bank;
- member Banks when the current scope is a Workspace;
- knowledge pages whose `mb_scope_id` belongs to that scope.

`recall --vault` explicitly expands to all eligible Markdown under `wiki/`.
Results identify their layer and scope, return bounded snippets, and require
`--expand` for full content. Bank/wiki contradictions are reported, not repaired.

### 8.6 `doctor`

Doctor is explicit and read-only. It validates configuration, vault contract,
catalog, bindings, Banks, identities, Workspace membership, locks/journals,
publish cursors, page IDs, managed hashes, attention freshness, size limits, and
sensitive-content findings.

There is no automatic repair in the initial v2 release. A future repair mode
must preview each mutation and require confirmation.

## 9. Knowledge Page Contract

### 9.1 Type And Location

The initial adapter supports the configured GavinObsidian vault in `generic`
mode. `wiki-mode.py route source <name>` must resolve under `wiki/sources/`.

Project pages use:

```yaml
type: source
source_type: repository
mb_scope: project
mb_scope_id: <project-id>
```

Workspace pages use:

```yaml
type: source
source_type: development-workspace
mb_scope: workspace
mb_scope_id: <workspace-id>
```

The adapter preserves unknown frontmatter and accepts only one page per
`mb_scope_id`. A duplicate ID blocks publish.

### 9.2 Managed Ownership

Only this block belongs to Memory Bank Lite:

```markdown
<!-- mb-lite:project-knowledge:start -->
...
<!-- mb-lite:project-knowledge:end -->
```

Workspace pages use a corresponding workspace marker. All bytes outside the
managed block remain unchanged except the adapter-owned flat `mb_*` frontmatter
fields and the standard scalar `updated` field. A new page receives the complete
standard source frontmatter, including `sources` entries that point to the
vault-relative Bank and repository URL where applicable. Missing, duplicate,
nested, or malformed markers fail closed.

The portable publish-state JSON stores the previous managed-block hash, Bank
revision, Git cursor(s), target path, and last successful transaction ID. A
manual managed-block edit creates a conflict that must be reviewed.

### 9.3 Incremental Merge

The managed block is a current synthesis, not an appended session log. Stable
sections are:

```text
Purpose And Boundary
Current Capability Boundary
Architecture And Key Paths
Stable Decisions And Rationale
Invariants And Attention
Verified Lessons
Superseded Knowledge
Long-Term Open Questions
```

Unchanged claims are skipped. Replaced claims move to `Superseded Knowledge`
with date and reason. Contradictions stop preview until the user resolves them.
A repeated content fingerprint is idempotent.

At 300 lines the preview warns and proposes an explicit later extraction of a
mature decision or concept. The adapter never splits pages automatically.

### 9.4 Shared Wiki Surfaces

- `wiki/index.md`: update its scalar `updated` value and idempotently
  insert/update one entry in `## Sources`.
- `wiki/sources/_index.md`: update its scalar `updated` value and idempotently
  insert/update one repository or development-workspace entry.
- `wiki/log.md`: update its scalar `updated` value and prepend one publish
  record; never edit historical records.
- `wiki/hot.md`: the only full-file cache refresh. Preview shows the complete
  diff; apply requires confirmation and keeps the cache bounded.

All wiki target locks are acquired through `scripts/wiki-lock.sh` in sorted path
order. The adapter then uses the preferred transport from
`.vault-meta/transport.json`, verifies the resulting bytes, and uses filesystem
fallback only when the declared fallback chain permits it.

## 10. Security And Privacy

Both done and publish perform deterministic prewrite scanning for high-confidence
private keys, API tokens, access keys, credential-bearing URLs, password-like
assignments, and forbidden secret-file inputs.

Rules:

- never read `.env`, authentication stores, key directories, or known secret
  files by default;
- store repository-relative key files in Banks;
- keep device absolute paths only in local bindings;
- render necessary published paths as `<repo>/...` or `<vault>/...`;
- block the entire transaction on a high-confidence finding;
- show the field/path and reason;
- never silently redact and continue;
- accept only a one-shot explicit `--allow-sensitive` override;
- never persist that override as configuration.

`--allow-sensitive` does not bypass path containment, revision checks, marker
validation, or transaction safety.

## 11. Concurrency And Recovery

Concurrency has three layers:

1. a device-local global Memory Bank Lite lock serializes catalog and compound
   transactions;
2. stable-order Bank locks serialize local Bank writers;
3. `claude-obsidian` per-file locks coordinate wiki pages with other vault
   writers.

Cross-device filesystem locks are not assumed to work through sync. Portable
catalog revisions, Bank revisions, page hashes, and publish-state cursors detect
divergence after synchronization.

Every multi-file mutation has a durable local journal containing expected and
original hashes plus protected backups. An incomplete journal blocks later
writes until Doctor reports it and a reviewed recovery action completes.

## 12. Migration

`/mb migrate --to-vault` is dry-run by default. It discovers legacy registries
and Banks, validates identities and content digests, scans for conflicts and
sensitive content, and shows old/new paths plus catalog and binding changes.

Confirmed apply:

1. copies Banks into vault-relative v2 locations without deleting sources;
2. validates tree hashes and required files;
3. adds an empty `attention.md` where missing without fabricating history;
4. writes the portable catalog and local bindings;
5. switches the configured runtime only after validation;
6. emits a transaction ID.

If a legacy Bank already occupies its final v2 target and lacks only
`attention.md`, dry-run remains read-only and confirmed apply adds just that
file. The transaction records the file as previously missing so any later
Catalog, Binding, or configuration failure removes it during rollback.

Cleanup is a separate dry-run and confirmation. Divergent Banks for one remote,
unresolved legacy roots, malformed registries, and ambiguous identities remain
blocking findings.

## 13. Compatibility And Acceptance

- Existing host invocation remains `/mb ...` for Claude Code/OpenCode and
  `$mb ...` for Codex.
- Existing v1 Bank Markdown remains readable before migration.
- v2 writes require a v2 catalog; no lazy or automatic migration occurs.
- Existing user content outside managed markers is byte-preserved.
- The CLI remains Python standard-library only and supports macOS and Linux.
- Current path-containment, symlink, fsync, rollback, revision-conflict, and
  installer ownership tests remain required baselines.
- Acceptance includes real Project and multi-worktree Workspace cycles against
  a disposable `claude-obsidian` fixture, followed by an opt-in real-vault test
  only after the user reviews the exact planned paths and diffs.
