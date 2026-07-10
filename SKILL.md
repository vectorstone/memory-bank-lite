---
name: mb
description: Explicit project memory. Use only when the user invokes mb init, start, done, or recall.
slash: true
---

# Memory Bank Lite

This skill never runs automatically.

The CLI exposes `init`, `start`, `recall`, and the confirmation-boundary
`done` workflow. Run the CLI from this skill's directory with
`python3 <skill-dir>/scripts/mb.py --project-root "$PWD"`.
Adopting an old Bank requires showing its key and path and obtaining explicit user confirmation before supplying `--confirm-adopt`.

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

Never initialize, read, or write a Bank unless the user explicitly invokes one of these commands.
