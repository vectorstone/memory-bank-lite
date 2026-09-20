# Memory Bank Lite

Memory Bank Lite provides explicitly invoked Project and Workspace memory for
Codex, Claude Code, and OpenCode. Operational Banks live under `memory-bank/`
inside a configured `claude-obsidian` vault. Stable knowledge reaches
`wiki/sources/` only through a reviewed `/mb publish`.

## Commands

```text
config set-vault PATH
init [--adopt PROJECT_ID]
init --workspace --name NAME [--member PATH ...] [--convert-project]
workspace add|remove PATH
start [--section SECTION]
done --revision|--apply
recall QUERY [--vault|--expand ID]
compact [--apply] [--project-id PROJECT_ID]
distill --revision|--apply
publish --snapshot|--preview|--apply
doctor
migrate --to-vault|--cleanup [--apply]
```

Every action is opt-in. The installer adds only shared skill links and the thin
Claude command wrapper. It does not add hooks, modify repositories, start
background processes, or manage vault Git commits.

New Banks contain only the four core files plus `notes/`. Every `done --apply`
rolls progress entries beyond 15 entries / 32 KB verbatim into
`archive/progress-<year>.md` and moves closed attention items into
`archive/attention.md`, so Banks stay bounded without losing history; `recall`
still searches the archive. `compact` applies the same housekeeping on demand
to Banks that predate the limits, and `distill` promotes stable conclusions
from progress entries into `lessons.md` (human-confirmed, boosted in recall).

Workspace initialization previews candidate members before `--confirm-members`
writes anything. If the exact Workspace root is already bound to a Project,
`--convert-project` is required. Conversion copies the Project Bank into the new
Workspace, keeps the source Project identity and Bank available to other
clones/worktrees, and atomically rebinds only the current root.

Migration is also dry-run by default. An existing v1 target that differs from
v2 only by a missing `attention.md` is upgraded during apply; a later failure
removes that file again as part of transaction rollback.

## Install

```bash
bash install.sh --vault-path /absolute/path/to/GavinObsidian
```

The vault path can be overridden per process with `MB_LITE_VAULT_PATH`.
Configuration uses `~/.config/memory-bank-lite/`; local root bindings and
transaction recovery state use `~/.local/state/memory-bank-lite/`.

Publishing uses the vault address allocator when it is usable. If its host lock
dependency is unavailable, the knowledge page remains addressless; the vault's
retrieval pipeline supplies its documented stable synthetic address instead.

See the [v2 design](docs/superpowers/specs/2026-07-19-memory-bank-lite-v2-design.md)
and [implementation plan](docs/superpowers/plans/2026-07-19-memory-bank-lite-v2-implementation.md)
for data contracts and acceptance gates.
