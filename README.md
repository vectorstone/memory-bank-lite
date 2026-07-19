# Memory Bank Lite

Memory Bank Lite provides explicitly invoked Project and Workspace memory for
Codex, Claude Code, and OpenCode. Operational Banks live under `memory-bank/`
inside a configured `claude-obsidian` vault. Stable knowledge reaches
`wiki/sources/` only through a reviewed `/mb publish`.

## Commands

```text
config set-vault PATH
init [--adopt PROJECT_ID]
init --workspace --name NAME [--member PATH ...]
workspace add|remove PATH
start [--section SECTION]
done --revision|--apply
recall QUERY [--vault|--expand ID]
publish --snapshot|--preview|--apply
doctor
migrate --to-vault|--cleanup [--apply]
```

Every action is opt-in. The installer adds only shared skill links and the thin
Claude command wrapper. It does not add hooks, modify repositories, start
background processes, or manage vault Git commits.

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
