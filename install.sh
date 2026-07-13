#!/usr/bin/env bash
set -euo pipefail

SOURCE="${MB_LITE_SOURCE:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
SOURCE="$(cd -- "$SOURCE" && pwd -P)"
AGENTS_SKILL="$HOME/.agents/skills/mb"
CLAUDE_SKILL="$HOME/.claude/skills/mb"
CLAUDE_COMMAND="$HOME/.claude/commands/mb.md"
SOURCE_COMMAND="$SOURCE/commands/mb.md"

if [[ ! -f "$SOURCE_COMMAND" || ! -r "$SOURCE_COMMAND" ]]; then
  printf 'Required source artifact is missing or unreadable: %s\n' "$SOURCE_COMMAND" >&2
  exit 1
fi

link_resolves_to_source() {
  local path="$1"
  local resolved

  [[ -L "$path" ]] || return 1
  resolved="$(cd -P -- "$path" 2>/dev/null && pwd -P)" || return 1
  [[ "$resolved" == "$SOURCE" ]]
}

require_owned_or_absent_link() {
  local path="$1"

  if [[ -e "$path" || -L "$path" ]]; then
    if ! link_resolves_to_source "$path"; then
      printf 'Refusing to replace non-owned path: %s\n' "$path" >&2
      return 1
    fi
  fi
}

require_owned_or_absent_link "$AGENTS_SKILL"
require_owned_or_absent_link "$CLAUDE_SKILL"
if [[ -e "$CLAUDE_COMMAND" || -L "$CLAUDE_COMMAND" ]]; then
  if [[ -L "$CLAUDE_COMMAND" ]] || ! cmp -s -- "$SOURCE_COMMAND" "$CLAUDE_COMMAND"; then
    printf 'Refusing to replace non-owned path: %s\n' "$CLAUDE_COMMAND" >&2
    exit 1
  fi
fi

mkdir -p -- "$(dirname -- "$AGENTS_SKILL")"
mkdir -p -- "$(dirname -- "$CLAUDE_SKILL")"
mkdir -p -- "$(dirname -- "$CLAUDE_COMMAND")"

if [[ ! -L "$AGENTS_SKILL" ]]; then
  ln -s -- "$SOURCE" "$AGENTS_SKILL"
fi
if [[ -L "$CLAUDE_SKILL" ]]; then
  rm -- "$CLAUDE_SKILL"
fi
ln -s -- "$AGENTS_SKILL" "$CLAUDE_SKILL"
cp -- "$SOURCE_COMMAND" "$CLAUDE_COMMAND"

printf 'Memory Bank Lite installed from %s\n' "$SOURCE"
