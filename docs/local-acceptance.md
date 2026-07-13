# Local Acceptance

## 1. Baseline

Capture the `onduty` worktree state, run the migration dry-run, and confirm the
worktree state is byte-identical afterward:

```bash
git -C /Users/wangxiaoguang/IdeaProjects/onduty status --short > /tmp/mb-lite-onduty-before.txt
bash migrate-cleanup.sh > /tmp/mb-lite-dry-run.txt
git -C /Users/wangxiaoguang/IdeaProjects/onduty status --short > /tmp/mb-lite-onduty-after.txt
cmp /tmp/mb-lite-onduty-before.txt /tmp/mb-lite-onduty-after.txt
```

Review `/tmp/mb-lite-dry-run.txt`. As applicable to the current machine, the
dry-run must list central registry creation or merge, old prompt blocks, old
hooks, old agents and commands, and registered project adapters. It must not
modify user configuration, project configuration, registrations, or Bank
content.

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
