---
name: mb
description: Explicit project memory. Use only when the user invokes mb init, start, done, or recall.
slash: true
---

# Memory Bank Lite

This skill never runs automatically.

Commands are `init`, `start`, `recall`, and `done`. Run the CLI from this skill's directory with `python3 <skill-dir>/scripts/mb.py --project-root "$PWD"`.
Adopting an old Bank requires showing its key and path and obtaining explicit user confirmation before supplying `--confirm-adopt`.

Never initialize, read, or write a Bank unless the user explicitly invokes one of these commands.
