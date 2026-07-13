from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from .bank import validate_bank
from .errors import MbError
from .identity import RepositoryIdentity, resolve_identity


@dataclass(frozen=True)
class PlannedProject:
    identity: RepositoryIdentity
    source_banks: tuple[Path, ...]
    target_bank: Path
    known_roots: tuple[Path, ...]


@dataclass(frozen=True)
class UnresolvedLegacyRecord:
    registry_path: Path
    root: Path
    bank_path: Path | None
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    projects: dict[str, PlannedProject]
    legacy_registries: tuple[Path, ...]
    retained_legacy_registries: tuple[Path, ...] = ()
    unresolved_records: tuple[UnresolvedLegacyRecord, ...] = ()

    @property
    def removable_legacy_registries(self) -> tuple[Path, ...]:
        retained = frozenset(self.retained_legacy_registries)
        return tuple(path for path in self.legacy_registries if path not in retained)


def legacy_registry_paths(home: Path) -> tuple[Path, ...]:
    return (
        home / ".codex/memory-bank/registry.json",
        home / ".claude/memory-bank/registry.json",
        home / ".config/opencode/memory-bank/registry.json",
    )


def tree_digest(bank: Path) -> str:
    digest = sha256()
    for path in sorted(
        value
        for value in bank.rglob("*")
        if value.is_file() and value.name != ".mb-lite.lock"
    ):
        digest.update(path.relative_to(bank).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _records(path: Path) -> list[tuple[Path, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt legacy registry: {path}", 4) from exc
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise MbError(f"corrupt legacy registry projects: {path}", 4)
    result: list[tuple[Path, object]] = []
    for root, raw in projects.items():
        result.append((Path(root), raw))
    return result


def _usable_bank_path(raw: object) -> Path | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("bank_path")
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def plan_migration(home: Path, central_home: Path) -> MigrationPlan:
    grouped: dict[str, tuple[RepositoryIdentity, list[Path], list[Path]]] = {}
    registries = tuple(path for path in legacy_registry_paths(home) if path.exists())
    retained_registries: list[Path] = []
    unresolved_records: list[UnresolvedLegacyRecord] = []
    for registry in registries:
        registry_is_retained = False
        for root, raw in _records(registry):
            raw_bank = _usable_bank_path(raw)
            if not root.is_dir():
                unresolved_records.append(
                    UnresolvedLegacyRecord(
                        registry,
                        root,
                        raw_bank,
                        (
                            "legacy project root does not exist"
                            if raw_bank is not None
                            else "legacy project root does not exist; "
                            "bank_path is unavailable"
                        ),
                    ),
                )
                registry_is_retained = True
                continue
            if raw_bank is None:
                raise MbError(f"invalid legacy registry entry for {root}", 4)
            identity = resolve_identity(root)
            bank = validate_bank(raw_bank.resolve())
            current = grouped.setdefault(identity.key, (identity, [], []))
            if bank not in current[1]:
                current[1].append(bank)
            if identity.root not in current[2]:
                current[2].append(identity.root)
        if registry_is_retained:
            retained_registries.append(registry)
    planned: dict[str, PlannedProject] = {}
    for key, (identity, banks, roots) in grouped.items():
        digests = {tree_digest(bank) for bank in banks}
        if len(digests) > 1:
            raise MbError(
                f"repository {key} has different Bank content across legacy registries",
                4,
            )
        target = central_home / "projects" / identity.project_id / ".memory-bank"
        planned[key] = PlannedProject(
            identity,
            tuple(sorted(banks)),
            target,
            tuple(sorted(roots)),
        )
    return MigrationPlan(
        planned,
        registries,
        tuple(retained_registries),
        tuple(unresolved_records),
    )
