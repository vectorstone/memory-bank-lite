---
name: mb
description: Explicit project memory. Use only when the user invokes mb init, start, done, done --yes, or recall.
slash: true
---

# Memory Bank Lite

This skill never runs automatically.

The CLI exposes `init`, `start`, `recall`, and the `done` write workflow. Run
the CLI from this skill's directory with
`python3 <skill-dir>/scripts/mb.py --project-root "$PWD"`.
Adopting an old Bank requires showing its key and path and obtaining explicit user confirmation before supplying `--confirm-adopt`.

## done

Treat `done --yes` as a one-shot fast-write authorization. Also use fast-write
mode when the same invocation explicitly says `directly write`, `write without
confirmation`, `skip confirmation`, `auto-write`, `直接写入`, `无需确认`,
`跳过确认`, `自动写入`, or an unambiguous equivalent in the user's language.
Do not infer fast-write mode from vague requests such as `save quickly`, and
never persist it as a preference. `--yes` is a skill-level argument; never pass
it to `mb.py`.

For every `done` mode:

1. Run `python3 <skill-dir>/scripts/mb.py --project-root "$PWD" done --revision --json`.
2. Inspect the current conversation and `git status --short` plus `git diff --stat`.
3. Draft JSON with the returned `bank_revision` and fields `summary`, `decisions`, `next_steps`, `blockers`, `key_files`, `completed_items`, `new_items`, and `note`.

In normal mode:

4. Show a readable draft to the user. Do not write any file.
5. Ask for explicit confirmation.
6. Only after confirmation, pipe the exact JSON to `done --apply --json`.
7. Report the applied summary, resulting revision, and note path.

If confirmation is absent, ambiguous, or the session ends, save nothing. If
apply returns exit code 3, regenerate and show the draft, then require fresh
confirmation because the payload changed.

In fast-write mode:

4. Do not show the draft before writing and do not ask for confirmation.
5. Pipe the exact JSON directly to `done --apply --json`.
6. Report the applied summary, resulting revision, and note path.

If fast-write apply returns exit code 3, fetch the new revision, inspect the
current state again, rebuild the payload, and retry once without asking. Stop
and report the error after a second revision conflict or any other validation
or write failure. Never bypass payload validation, key-file containment,
revision checks, locking, or atomic writes in either mode.

Never initialize, read, or write a Bank unless the user explicitly invokes one of these commands.
