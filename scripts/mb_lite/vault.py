import json
from pathlib import Path

from .errors import MbError


REQUIRED_FILES = (
    "WIKI.md",
    ".vault-meta/transport.json",
    "wiki/index.md",
    "wiki/log.md",
    "wiki/hot.md",
    "wiki/sources/_index.md",
    "scripts/wiki-lock.sh",
    "scripts/wiki-mode.py",
)


def contained_path(root: Path, relative: str, prefix: str | None = None) -> Path:
    lexical = Path(relative)
    if lexical.is_absolute() or not lexical.parts or any(
        part in {"", ".", ".."} for part in lexical.parts
    ):
        raise MbError(f"invalid vault-relative path: {relative}", 4)
    if prefix is not None and lexical.parts[0] != prefix:
        raise MbError(f"vault-relative path must stay under {prefix}/: {relative}", 4)
    root = Path(root)
    if root.is_symlink():
        raise MbError(f"vault root must not be a symlink: {root}", 4)
    root = root.resolve()
    target = root.joinpath(*lexical.parts)
    current = root
    for index, part in enumerate(lexical.parts):
        current = current / part
        if current.is_symlink():
            raise MbError(f"vault path must not traverse a symlink: {relative}", 4)
        if index < len(lexical.parts) - 1 and current.exists() and not current.is_dir():
            raise MbError(f"vault path parent is not a directory: {relative}", 4)
    parent = target.parent.resolve()
    try:
        parent.relative_to(root)
    except ValueError as exc:
        raise MbError(f"vault-relative path escapes vault: {relative}", 4) from exc
    return target


def validate_vault_contract(vault: Path) -> Path:
    root = Path(vault)
    try:
        observed = root.lstat()
    except OSError as exc:
        raise MbError(f"Obsidian vault does not exist: {root}", 2) from exc
    if root.is_symlink() or not root.is_dir():
        raise MbError(f"Obsidian vault must be a real directory: {root}", 4)
    root = root.resolve()
    for relative in REQUIRED_FILES:
        path = contained_path(root, relative)
        if not path.is_file():
            raise MbError(f"incompatible claude-obsidian vault; missing {relative}", 4)
    try:
        transport = json.loads((root / ".vault-meta/transport.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MbError("incompatible claude-obsidian transport.json", 4) from exc
    if not isinstance(transport, dict) or not isinstance(transport.get("fallback_chain"), list):
        raise MbError("incompatible claude-obsidian transport.json", 4)
    return root
