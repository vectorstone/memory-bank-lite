#!/usr/bin/env bash
set -euo pipefail

SOURCE="${MB_LITE_SOURCE:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
SOURCE="$(cd -- "$SOURCE" && pwd -P)"
AGENTS_SKILL="$HOME/.agents/skills/mb"
CLAUDE_SKILL="$HOME/.claude/skills/mb"
CLAUDE_COMMAND="$HOME/.claude/commands/mb.md"
OWNERSHIP_DESCRIPTION='description: Explicit Memory Bank Lite action'

remove_owned_link() {
  local path="$1"
  local resolved

  [[ -L "$path" ]] || return 0
  resolved="$(cd -P -- "$path" 2>/dev/null && pwd -P)" || return 0
  if [[ "$resolved" == "$SOURCE" ]]; then
    rm -- "$path"
  fi
}

remove_owned_link "$CLAUDE_SKILL"
remove_owned_link "$AGENTS_SKILL"

if [[ -f "$CLAUDE_COMMAND" && ! -L "$CLAUDE_COMMAND" ]] \
  && grep -Fqx -- "$OWNERSHIP_DESCRIPTION" "$CLAUDE_COMMAND"; then
  rm -- "$CLAUDE_COMMAND"
fi

printf 'Memory Bank Lite source links removed; registry and Bank data preserved.\n'
