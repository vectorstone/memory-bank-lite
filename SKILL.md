---
name: mb
description: Explicit Project and Workspace memory. Use only when the user invokes an mb action.
slash: true
---

# Memory Bank Lite

This skill never runs automatically. Never read or write a Bank, inspect the
configured vault, or run Doctor unless the user explicitly invokes an `mb`
action in the current message.

Run the CLI from this skill directory:

```bash
python3 <skill-dir>/scripts/mb.py --project-root "$PWD" <action>
```

## Configuration And Init

- `config set-vault <absolute-path>` validates and records a compatible
  `claude-obsidian` vault.
- `init` creates or reuses a Project Bank without writing into the repository.
- `init --adopt <project-id>` is read-only until the CLI output has been shown
  and the user explicitly confirms; only then pass `--confirm-adopt`.
- `init --workspace --name <name>` first returns candidate members and writes
  nothing. Show the candidates and obtain confirmation before rerunning with
  `--confirm-members`.
- Workspace membership changes occur only through explicit `workspace add` or
  `workspace remove` invocations.

## Start And Recall

- `start` is strictly read-only. A Workspace start includes bounded member
  summaries.
- `recall <query>` searches the current scope's Banks and bound published pages.
- Add `--vault` only when the user explicitly requests full-vault search.
- Use `--expand <result-id>` only after a result was returned or when the user
  explicitly supplies the ID.

## Done

Treat `done --yes` as a one-shot fast-write authorization. Also recognize only
unambiguous equivalents such as `write without
confirmation`, `directly write`, `直接写入`, `无需确认`, `跳过确认`, or `自动写入`.
Do not infer fast-write mode from vague requests and never persist it as a preference.
`--yes` is a skill-level argument; never pass
it to `mb.py`.

For Project done:

1. Run `done --revision --json`.
2. Inspect the current conversation, `git status --short`, and
   `git diff --stat`.
3. Draft the existing done payload with `bank_revision`, `summary`,
   `decisions`, `next_steps`, `blockers`, repository-relative `key_files`,
   `completed_items`, `new_items`, `note`, and `attention_changes`.

For Workspace done:

1. Run `done --revision --json` and inspect its Workspace/member revisions,
   prior portable `closeout_state`, bindings, Git status, branches, and commits.
2. Include one Workspace update and only the affected Project updates.
3. Wrap them in `workspace_id`, `catalog_revision`, and `updates`.
4. Never invent a Project update when affected-scope evidence is ambiguous.

In normal mode:

1. Show one readable draft.
2. Ask for explicit confirmation.
3. Pipe the exact JSON to `done --apply --json` only after confirmation.
4. If a revision conflict changes the payload, require fresh
confirmation because the payload changed.

In fast mode, Do not show the draft before writing. Pipe the payload directly.
On a revision conflict, rebuild evidence and retry once without asking. Stop
after a second revision conflict or any other validation failure.

Never bypass path containment, sensitive-content scanning, Bank revisions,
locks, or compound transaction rollback. A one-shot `--allow-sensitive` may be
passed only when the same user invocation explicitly authorizes it after seeing
the finding.

## Publish

Publish promotes stable knowledge; it never copies current tasks, next steps,
blockers, or ordinary recent progress. Publish has no fast mode.

1. Run `publish --snapshot --json`.
2. Read only the returned Bank paths, current bound knowledge pages, Git
   evidence since the saved cursor, and key files needed to verify a claim.
3. For each changed scope, draft `managed_markdown` with exactly these sections:
   `Purpose And Boundary`, `Current Capability Boundary`,
   `Architecture And Key Paths`, `Stable Decisions And Rationale`,
   `Invariants And Attention`, `Verified Lessons`, `Superseded Knowledge`, and
   `Long-Term Open Questions`.
4. Create a complete replacement for `wiki/hot.md` that preserves useful recent
   context and adds only the stable publish result.
5. Build the JSON payload using the snapshot's `catalog_revision`,
   `transaction_id`, `expected_hashes`, per-scope `bank_revision`, `page_hash`,
   and `git_cursors`.
6. Pipe it to `publish --preview --json`. This must write nothing.
7. Show every changed file and readable diff. Ask for explicit confirmation.
8. Only after confirmation, pipe the exact same payload to
   `publish --apply --json`.

A changed catalog, Bank, page, shared wiki surface, managed block, transport, or
Git ancestry invalidates the preview. Regenerate rather than forcing the apply.
Do not call another Agent skill as the publish implementation.

## Doctor And Migration

- `doctor` is explicit and read-only. Do not repair findings automatically.
- `migrate --to-vault` is dry-run by default. Show its complete source/target
  map and obtain confirmation before adding `--apply`.
- Migration copies and validates Banks; it never removes the source data.
- `migrate --cleanup` remains a separate dry-run operation with its own review
  and confirmation before `--apply` moves legacy data to a recovery backup.

No command in this skill installs hooks, starts background work, or manages
vault Git commits.
