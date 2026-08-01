# Memory Bank Lite v2 Implementation Plan

**Date:** 2026-07-19
**Design:** `docs/superpowers/specs/2026-07-19-memory-bank-lite-v2-design.md`
**Status:** Implemented; automated and disposable real-contract acceptance completed

Implementation follows the phase boundaries below. Final verification covers
the full unittest suite, Python compilation, shell syntax, diff hygiene, and a
disposable copy of the configured GavinObsidian contract. The real vault itself
is not a publish target during acceptance.

## 1. Delivery Strategy

Implement v2 as vertical slices. Every phase must keep the existing test suite
green, add failure-path coverage proportional to its write surface, and produce
a usable CLI boundary before the next phase begins.

Do not start by expanding `scripts/mb.py`. First extract stable services from
the current single-scope wiring, then add Workspace and publish behavior on top.

## 2. Target Module Map

Retain and evolve:

```text
scripts/mb_lite/bank_fs.py       secure contained file access
scripts/mb_lite/bank.py          Bank validation and initialization
scripts/mb_lite/identity.py      Git remote normalization and Project evidence
scripts/mb_lite/done.py          single-Bank render functions
scripts/mb_lite/start.py         bounded context rendering
scripts/mb_lite/recall.py        deterministic Markdown retrieval
scripts/mb_lite/transaction.py   durable backup/recovery primitives
```

Replace or split:

```text
scripts/mb_lite/registry.py      -> catalog.py + bindings.py
scripts/mb.py                    -> parser/dispatch only
```

Add:

```text
scripts/mb_lite/config.py
scripts/mb_lite/catalog.py
scripts/mb_lite/bindings.py
scripts/mb_lite/scope.py
scripts/mb_lite/workspace.py
scripts/mb_lite/attention.py
scripts/mb_lite/security.py
scripts/mb_lite/compound.py
scripts/mb_lite/git_evidence.py
scripts/mb_lite/publish.py
scripts/mb_lite/frontmatter.py
scripts/mb_lite/vault.py
scripts/mb_lite/transport.py
scripts/mb_lite/doctor.py
scripts/mb_lite/migrate_v2.py
```

`frontmatter.py` is deliberately narrow: parse and update only flat scalar
`mb_*` keys while preserving all unknown YAML bytes. Do not implement a general
YAML parser and do not reserialize user frontmatter.

## 3. Phase 0 - Freeze The v1 Baseline

Plan-authoring baseline on 2026-07-19: the full unittest discovery command
passed all 145 tests in 4.679 seconds on macOS.

### Tasks

1. Run the complete existing unit suite and record its count and duration.
2. Add a golden fixture for a representative legacy Bank containing custom text
   around every current managed marker.
3. Add a CLI snapshot fixture for current `init`, `start`, `done`, and `recall`
   JSON output.
4. Document that no current source link or default data directory is installed
   on this machine; do not install or migrate as part of test setup.

### Gate

- Existing behavior is reproducible before schema changes.
- No test writes outside temporary directories.

## 4. Phase 1 - Portable Configuration, Catalog, And Bindings

### Tasks

1. Implement XDG-compatible config/state path resolution in `config.py`.
2. Implement vault precedence: environment -> config -> actionable failure.
3. Validate the target contract without mutation:
   `WIKI.md`, `.vault-meta/transport.json`, `wiki/index.md`, `wiki/log.md`,
   `wiki/hot.md`, `wiki/sources/_index.md`, `scripts/wiki-lock.sh`, and
   `scripts/wiki-mode.py`.
4. Implement strict v2 `catalog.json` parsing, containment, atomic replacement,
   file/parent fsync, and expected-revision writes.
5. Implement device-local `bindings.json` with separate Project and Workspace
   maps and multiple worktrees per Project.
6. Refactor `identity.py` so normalized remote evidence and stable Project ID are
   separate concepts. Preserve all existing remote normalization tests.
7. Implement no-remote Project creation and explicit adopt.
8. Add `config set-vault` and refactor `init` onto the new services.
9. Update `install.sh` with `--vault-path` as an optional config write. Preserve
   non-owned-path refusal and never initialize vault content from the installer.

### Tests

- environment/config precedence and malformed configuration;
- target-contract validation with missing, symlinked, or malformed files;
- relative-path catalog containment and unknown-version failure;
- duplicate repository aliases and duplicate IDs;
- concurrent catalog updates with stale expected revisions;
- no-remote adopt across two simulated devices;
- remote-added/remote-changed confirmation boundaries;
- installer idempotency and non-owned config refusal.

### Gate

- Project `init/start/done/recall` work through v2 catalog/bindings in temporary
  vault fixtures.
- No portable file contains a device absolute root.

## 5. Phase 2 - v2 Bank Schema And Attention

### Tasks

1. Add `templates/.memory-bank/attention.md` with an empty managed area.
2. Extend Bank validation with v1-readable and v2-writable modes.
3. Replace the three-core-file revision with a contained stable Markdown-tree
   digest. Ignore only declared lock/temp artifacts.
4. Implement strict attention parsing, stable ID allocation, status transitions,
   review due calculation, and marker rendering.
5. Extend done payloads with `attention_changes` while keeping current fields
   backward-readable at the skill draft layer.
6. Add active attention to `start` with severity-aware trimming under 8 KiB.
7. Extend recall result metadata with source layer and scope ID.

### Tests

- custom content outside attention markers is byte-preserved;
- malformed, duplicate, or nested markers fail closed;
- add/update/resolve/supersede transitions and unknown IDs;
- review due does not mutate state;
- manual note/attention edits change Bank revision;
- deterministic 8 KiB trimming and omitted-entry counts;
- symlinked attention/optional sources cannot escape the Bank.

### Gate

- Single-Project v2 behavior is complete before Workspace writes are added.

## 6. Phase 3 - Workspace Scope And Aggregated Start

### Tasks

1. Implement explicit Workspace creation with generated stable ID.
2. Add bounded immediate-child discovery and repeated `--member` inputs.
3. Render a candidate membership plan; require confirmation before catalog and
   binding mutation.
4. Add explicit `workspace add`, `workspace remove`, and cross-device adopt.
5. Deduplicate multiple worktrees by Project ID while retaining checkout data in
   local bindings.
6. Implement exact-root Workspace scope resolution without changing Project
   resolution inside member worktrees.
7. Build Workspace `start` from its own Bank plus bounded member summaries.
8. Add scoped `recall` across the Workspace Bank and member Banks.

### Tests

- a non-Git Workspace root with two linked worktrees;
- multiple worktrees of one repository count as one member;
- membership does not change during `start`;
- member add/remove does not change Workspace ID;
- exact Workspace root versus nested member scope selection;
- missing member checkout is reported but does not corrupt portable membership;
- output remains bounded for large member sets.

### Gate

- A user can start at a multi-repository Workspace root and recover both
  cross-repository state and compact member state without changing files.

## 7. Phase 4 - Security Scanner And Compound Done

### Tasks

1. Implement deterministic high-confidence scanning in `security.py`.
2. Add forbidden input path classes and published path normalization.
3. Define `CompoundDonePayload` with scope ID, expected catalog revision, a list
   of per-Bank expected revisions/updates, Git evidence, and one-shot override.
4. Implement affected-Project evidence collection:
   current-session paths supplied by the skill, member Git status, and commits
   since the last Workspace closeout baseline.
5. In normal mode, render a single human-readable Workspace/Project draft that
   supports excluding false-positive members before apply.
6. In `--yes` mode, fail when affected-scope evidence is ambiguous.
7. Generalize current staged writes and rollback into a durable compound
   transaction with a local global lock, stable Bank-lock order, journal,
   expected hashes, and transaction ID.
8. Store Workspace per-member closeout baselines only after full success.
   Persist them in `memory-bank/closeout-state/<workspace-id>.json`, not in the
   identity catalog.

### Tests

- scanner true positives, known false positives, and one-shot override;
- no secret file is read merely to scan it;
- three-Bank successful apply and unchanged-member exclusion;
- stale revision in the final Bank causes zero writes;
- failure after one replacement restores every earlier file;
- interrupted transaction is detected and blocks later writes;
- concurrent Workspaces updating one Project permit one winner;
- `done --yes` does not persist its authorization.

### Gate

- One Workspace `done` can safely update itself and all affected Project Banks
  with one confirmation or one explicit `--yes`.

## 8. Phase 5 - Narrow `claude-obsidian` Publish Adapter

### Tasks

1. Implement `VaultContract` validation and load the target mode and transport.
2. Require generic mode for the first release and verify source routing stays
   under `wiki/sources/`.
3. Implement strict lookup by `mb_scope_id`, duplicate detection, safe filename
   generation, and moved-page recovery.
4. Implement byte-preserving flat `mb_*` frontmatter patching.
5. Implement Project/Workspace managed-block parsing, hashing, rendering, and
   manual-edit conflicts.
6. Add portable publish-state JSON for page path, managed hash, Bank revision,
   Git cursor(s), content fingerprint, and transaction ID.
7. Implement bounded Git evidence from the last cursor, including ancestry and
   rebase detection. Do not read raw full diffs unless needed to verify a draft
   claim.
8. Define `PublishPayload` and the snapshot/preview/apply protocol.
9. Render deterministic changes to:
   knowledge page(s), `wiki/index.md`, `wiki/sources/_index.md`, `wiki/log.md`,
   `wiki/hot.md`, and publish-state file(s). Patch standard scalar `updated`
   fields without reserializing frontmatter.
10. Acquire the Memory Bank Lite global lock, then all target `wiki-lock.sh`
    locks in sorted order. Recheck expected hashes after locking.
11. Implement CLI-preferred transport with declared fallback, post-write byte
    verification, journaled rollback, and recovery blocking.
12. Enforce one confirmation for a Workspace page plus all affected Project
    pages. Never add a publish fast path.

### Tests

- new Project and Workspace page creation;
- repeated identical publish is a no-op;
- incremental section update and superseded-claim movement;
- Project-local versus Workspace-cross-cutting routing;
- bytes outside managed blocks remain identical;
- manual managed-block edits, duplicate IDs, bad markers, and moved pages;
- index/sub-index idempotency and log prepend preservation;
- complete hot-cache preview and bounded replacement;
- cursor ancestry, rebase, changed Bank revision, and changed shared wiki file;
- wiki lock contention and stable lock order;
- CLI success, allowed filesystem fallback, unavailable transport, partial
  failure rollback, and post-write verification failure;
- sensitive-content block across a multi-page Workspace publish.

### Gate

- A confirmed publish updates all required `claude-obsidian` surfaces in one
  recoverable transaction; an unconfirmed or stale preview writes nothing.

## 9. Phase 6 - Cross-Layer Recall And Doctor

### Tasks

1. Extend default recall to current-scope Banks and bound knowledge pages.
2. Add explicit `--vault` search with containment, result caps, stable IDs, and
   source labels.
3. Detect obvious Bank/published-knowledge conflicts without auto-resolution.
4. Implement read-only Doctor checks from the design.
5. Produce both readable and JSON Doctor reports with stable check IDs and
   severity.
6. Do not add repair behavior in this phase.

### Tests

- scope-bounded default results and explicit full-vault expansion;
- no symlink/path escape during search or expand;
- deterministic result order and caps;
- conflict reporting without writes;
- every Doctor finding against targeted corrupt fixtures;
- byte-for-byte read-only Doctor verification.

### Gate

- The published layer is useful from project workflows without making `start`
  heavier or enabling global retrieval implicitly.

## 10. Phase 7 - Explicit v1-to-v2 Migration

### Tasks

1. Build `migrate_v2.py` on the existing discovery, digest, containment, and
   `BackupTransaction` primitives.
2. Discover the current central v1 registry plus historical client registries.
3. Produce a dry-run containing source/target mappings, identity evidence,
   conflicts, sensitive findings, and unresolved records.
4. Copy to temporary target trees, validate digests, add empty attention files,
   and atomically place final Project directories.
5. Write catalog and local bindings only after every copied Bank validates.
6. Switch config only at the final commit point.
7. Preserve all source Banks and legacy registries.
8. Add a separate cleanup preview/apply path with its own confirmation and
   rollback transaction.
9. Keep the existing legacy adapter cleanup behavior isolated from v2 data
   migration so one can succeed without authorizing the other.

### Tests

- current v1 registry to vault catalog;
- historical per-client registries;
- same remote/same digest coalescing;
- same remote/different digest blocking;
- missing roots and unresolved records retained;
- no-remote projects requiring explicit identity selection;
- copy/fsync/config-switch failures at every mutation boundary;
- cleanup cannot remove v2 or user-owned content;
- rollback and second-run idempotency.

### Gate

- Migration is non-destructive by default and source cleanup is independently
  authorized.

## 11. Phase 8 - Skill Protocol, Documentation, And Real Acceptance

### Tasks

1. Update `SKILL.md` and `commands/mb.md` with the exact explicit-trigger and
   confirmation protocols for all actions.
2. Keep host wrappers thin; do not add host hooks or project adapters.
3. Replace hard-coded default-home documentation with portable config examples.
4. Update install, uninstall, migration, and troubleshooting documentation.
5. Extend `docs/local-acceptance.md` with disposable-vault and Workspace cycles.
6. Run the complete suite on macOS and Linux-compatible CI.
7. Run a real disposable-vault cycle using the checked-in GavinObsidian contract
   shape: configure -> Project init -> Workspace init -> start -> compound done ->
   publish preview -> confirm -> recall -> Doctor -> rollback drill.
8. Before any real GavinObsidian write, show the user the exact target paths and
   diffs and obtain separate acceptance approval.

### Gate

- All automated suites pass.
- No implicit trigger is present in installed configuration.
- Real disposable-vault behavior matches the documented protocol.
- The existing GavinObsidian vault is untouched unless separately approved.

## 12. Required Review Checkpoints

Run focused review after these boundaries:

1. catalog/bindings identity and containment;
2. compound transaction rollback and crash recovery;
3. publish marker/frontmatter preservation;
4. transport/lock ordering and shared wiki updates;
5. migration cleanup ownership.

At each checkpoint, distinguish passing tests from production readiness. Do not
advance while a known path escape, partial-write, identity collision, or silent
trigger remains.

## 13. Recommended Commit Sequence

Keep commits phase-local so rollback remains understandable:

```text
refactor: separate portable config and scope identity
feat: add portable catalog and local bindings
feat: add attention memory and v2 bank revisions
feat: add explicit workspace scopes
feat: add secure compound workspace closeout
feat: add reviewed obsidian knowledge publishing
feat: add scoped cross-layer recall and doctor
feat: add explicit v1 to v2 migration
docs: finalize v2 skill protocol and acceptance
```

Do not mix installer migration cleanup with publish implementation in one commit.

## 14. Post-v2 Scope Conversion And In-place Upgrade

1. Add explicit `init --workspace ... --convert-project` preview and confirmation
   handling for roots that already have an exact Project Binding.
2. Preserve the source Project record and Bank while copying its operational
   state into the new Workspace and rebinding only the current root.
3. Journal Catalog and Binding changes, conditionally replace the Binding, and
   remove all newly created scope directories after a confirmed rollback.
4. Upgrade an existing v1 migration target only by adding missing
   `attention.md`, recording that missing file in the rollback manifest before
   any mutation.
5. Cover read-only previews, invalid argument combinations, successful apply,
   byte preservation, and injected late-write failures.
