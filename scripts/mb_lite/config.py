from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from .errors import MbError


@dataclass(frozen=True)
class RuntimePaths:
    vault: Path
    config_root: Path
    state_root: Path


def config_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    base = Path(values.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base.expanduser() / "memory-bank-lite"


def state_root(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    base = Path(values.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base.expanduser() / "memory-bank-lite"


def _read_config(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MbError(f"invalid Memory Bank Lite config: {path}: {exc}", 4) from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "vault_path"}:
        raise MbError(f"invalid Memory Bank Lite config schema: {path}", 4)
    if value.get("schema_version") != 1 or not isinstance(value.get("vault_path"), str):
        raise MbError(f"invalid Memory Bank Lite config schema: {path}", 4)
    return value


def resolve_runtime(env: Mapping[str, str] | None = None) -> RuntimePaths:
    values = os.environ if env is None else env
    cfg_root = config_root(values)
    local_state = state_root(values)
    override = values.get("MB_LITE_VAULT_PATH", "").strip()
    if override:
        raw_vault = override
    else:
        data = _read_config(cfg_root / "config.json")
        raw_vault = str(data.get("vault_path", "")).strip()
    if not raw_vault:
        raise MbError(
            "Obsidian vault is not configured; run /mb config set-vault <path> "
            "or set MB_LITE_VAULT_PATH",
            2,
        )
    vault = Path(raw_vault).expanduser()
    if not vault.is_absolute():
        raise MbError("configured Obsidian vault path must be absolute", 4)
    try:
        vault = vault.resolve(strict=True)
    except OSError as exc:
        raise MbError(f"configured Obsidian vault does not exist: {vault}", 2) from exc
    return RuntimePaths(vault, cfg_root, local_state)


def _atomic_json(path: Path, value: dict[str, object], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError as exc:
        raise MbError(f"unable to write configuration: {path}: {exc}", 4) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def write_vault_config(vault: Path, env: Mapping[str, str] | None = None) -> Path:
    candidate = Path(vault).expanduser()
    if not candidate.is_absolute():
        raise MbError("Obsidian vault path must be absolute", 1)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MbError(f"Obsidian vault does not exist: {candidate}", 1) from exc
    path = config_root(env) / "config.json"
    _atomic_json(path, {"schema_version": 1, "vault_path": str(resolved)})
    return path
