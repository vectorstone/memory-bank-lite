from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from .errors import MbError


MARKER_PAIRS = (
    ("<!-- memory-bank-codex:start -->", "<!-- memory-bank-codex:end -->"),
    ("# [MEMORY-BANK-SKILL]", "<!-- /memory-bank-skill -->"),
    ("<!-- memory-bank:start -->", "<!-- memory-bank:end -->"),
    ("<!-- memory-bank-cursor:start -->", "<!-- memory-bank-cursor:end -->"),
    ("<!-- memory-bank-pi:start -->", "<!-- memory-bank-pi:end -->"),
)


def strip_known_blocks(source: str) -> tuple[str, bool]:
    result = source
    changed = False
    for start, end in MARKER_PAIRS:
        starts = result.count(start)
        ends = result.count(end)
        if starts != ends or starts == 0:
            continue
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end) + r"\n?",
            re.DOTALL,
        )
        result, count = pattern.subn("", result)
        changed = changed or count > 0
    return result, changed


def _managed_hook(hook: Any) -> bool:
    return isinstance(hook, dict) and "[memory-bank-skill]" in str(
        hook.get("command", ""),
    )


def clean_hooks_json(source: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(source)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                kept.append(entry)
                continue
            inner = [hook for hook in entry["hooks"] if not _managed_hook(hook)]
            if inner:
                updated = dict(entry)
                updated["hooks"] = inner
                kept.append(updated)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result


def manifest_owned_files(project: Path, manifest: Path) -> tuple[Path, ...]:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt adapter manifest: {manifest}", 4) from exc
    files = data.get("files", [])
    if not isinstance(files, list) or any(not isinstance(value, str) for value in files):
        raise MbError(f"corrupt adapter manifest files: {manifest}", 4)
    root = project.resolve()
    result: list[Path] = []
    for raw in files:
        lexical = Path(str(raw))
        if not lexical.is_absolute():
            lexical = project / lexical
        path = lexical.parent.resolve() / lexical.name
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise MbError(f"manifest path escapes project: {path}", 4) from exc
        result.append(path)
    return tuple(result)


def remove_opencode_plugin(source: dict[str, Any], plugin_ref: str) -> dict[str, Any]:
    result = deepcopy(source)
    plugins = result.get("plugin")
    if isinstance(plugins, list):
        kept = [value for value in plugins if value != plugin_ref]
        if kept:
            result["plugin"] = kept
        else:
            result.pop("plugin", None)
    return result
