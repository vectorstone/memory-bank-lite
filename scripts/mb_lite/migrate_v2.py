from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .bank import initialize_bank, upgrade_bank_to_v2, validate_bank, validate_v2_bank
from .bindings import BindingStore
from .catalog import CatalogStore, remote_project_id
from .config import _atomic_json
from .errors import MbError
from .identity import _slug
from .security import scan_text
from .transaction import BackupTransaction
from .vault import contained_path


@dataclass(frozen=True)
class V2MigrationItem:
    repository_key: str
    project_id: str
    display_name: str
    source_bank: str
    target_bank: str
    known_roots: tuple[str, ...]


def _compatible_digest(bank: Path) -> str:
    digest = sha256()
    paths = sorted(bank.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise MbError(f"legacy Bank contains symlinks: {bank}", 4)
    for path in (
        value for value in paths
        if value.is_file() and value.name not in {".mb-lite.lock", "attention.md"}
    ):
        digest.update(path.relative_to(bank).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _legacy_registry(legacy_home: Path) -> dict[str, object]:
    path = Path(legacy_home) / "registry.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MbError(f"legacy registry does not exist: {path}", 2) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MbError(f"invalid legacy registry: {path}", 4) from exc
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("projects"), dict):
        raise MbError(f"invalid legacy registry schema: {path}", 4)
    return raw


def plan_v2_migration(legacy_home: Path, catalog: CatalogStore) -> tuple[V2MigrationItem, ...]:
    raw = _legacy_registry(legacy_home)
    projects = raw["projects"]
    assert isinstance(projects, dict)
    items: list[V2MigrationItem] = []
    seen: dict[str, str] = {}
    for repository_key, value in sorted(projects.items()):
        if not isinstance(repository_key, str) or not isinstance(value, dict):
            raise MbError("invalid legacy project entry", 4)
        raw_bank = value.get("bank_path")
        roots = value.get("known_roots", [])
        if not isinstance(raw_bank, str) or not Path(raw_bank).is_absolute() or not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
            raise MbError(f"invalid legacy project entry: {repository_key}", 4)
        source = validate_bank(Path(raw_bank).resolve())
        project_id = value.get("project_id")
        if not isinstance(project_id, str) or not project_id.endswith(sha256(repository_key.encode("utf-8")).hexdigest()[:12]):
            project_id = remote_project_id(_slug(repository_key), repository_key)
        digest = _compatible_digest(source)
        if repository_key in seen and seen[repository_key] != digest:
            raise MbError(f"repository {repository_key} has divergent legacy Banks", 4)
        seen[repository_key] = digest
        target_relative = f"memory-bank/projects/{project_id}/.memory-bank"
        target = contained_path(catalog.vault, target_relative, "memory-bank")
        if target.exists():
            validate_bank(target)
            # Missing attention is the only expected v1/v2 difference.
            comparable = target
            if _compatible_digest(comparable) != digest:
                raise MbError(f"v2 target already contains different Bank content: {project_id}", 4)
        items.append(
            V2MigrationItem(
                repository_key,
                project_id,
                _slug(repository_key),
                str(source),
                target_relative,
                tuple(sorted(set(roots))),
            )
        )
    return tuple(items)


def migration_preview(items: tuple[V2MigrationItem, ...], legacy_home: Path) -> dict[str, object]:
    return {
        "mode": "dry-run",
        "legacy_home": str(Path(legacy_home).resolve()),
        "projects": [asdict(item) for item in items],
        "writes": [item.target_bank for item in items] + ["memory-bank/catalog.json", "local bindings.json"],
        "deletes": [],
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_bank(source: Path, target: Path) -> bool:
    if target.exists():
        upgrade_bank_to_v2(target)
        validate_v2_bank(target)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".migrate-{uuid4().hex}"
    try:
        shutil.copytree(
            source,
            temporary,
            symlinks=True,
            ignore=shutil.ignore_patterns(".mb-lite.lock", "__pycache__", "*.pyc"),
        )
        initialize_bank(temporary)
        validate_v2_bank(temporary)
        # Every symlink in a legacy Bank is rejected before final placement.
        if any(path.is_symlink() for path in temporary.rglob("*")):
            raise MbError(f"legacy Bank contains symlinks: {source}", 4)
        os.rename(temporary, target)
        _fsync_directory(target.parent)
        temporary = Path()
        return True
    finally:
        if str(temporary) not in {"", "."} and temporary.exists():
            shutil.rmtree(temporary)


def apply_v2_migration(
    items: tuple[V2MigrationItem, ...],
    catalog: CatalogStore,
    bindings: BindingStore,
    state_root: Path,
    config_path: Path,
) -> dict[str, object]:
    for item in items:
        source = Path(item.source_bank)
        for path in source.rglob("*.md"):
            if path.is_file() and not path.is_symlink():
                try:
                    findings = scan_text(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError):
                    findings = ()
                if findings:
                    raise MbError(f"sensitive content blocks migration: {path}", 1)
    missing_attention: list[Path] = []
    for item in items:
        target = contained_path(catalog.vault, item.target_bank, "memory-bank")
        if not target.exists():
            continue
        attention = target / "attention.md"
        try:
            attention.lstat()
        except FileNotFoundError:
            missing_attention.append(attention)
    transaction_id = "migrate-" + uuid4().hex[:12]
    transaction = BackupTransaction.create(Path(state_root) / "transactions", transaction_id)
    for path in (catalog.path, bindings.path, config_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            transaction.capture(path, "v2-migration")
        else:
            transaction.capture_missing(path, "v2-migration")
    for attention in missing_attention:
        transaction.capture_missing(attention, "v2-bank-upgrade")
    transaction.finish()
    created_targets: list[Path] = []
    try:
        for item in items:
            target = contained_path(catalog.vault, item.target_bank, "memory-bank")
            if _copy_bank(Path(item.source_bank), target):
                created_targets.append(target)
        for item in items:
            catalog.add_project(item.project_id, item.display_name, (item.repository_key,))
            for raw_root in item.known_roots:
                root = Path(raw_root)
                if root.is_dir():
                    bindings.bind(root, "project", item.project_id)
        _atomic_json(config_path, {"schema_version": 1, "vault_path": str(catalog.vault)})
    except Exception as migration_error:
        rollback_errors: list[str] = []
        try:
            BackupTransaction.rollback(transaction.directory)
        except Exception as exc:
            rollback_errors.append(str(exc))
        for target in reversed(created_targets):
            try:
                shutil.rmtree(target)
                _fsync_directory(target.parent)
            except OSError as exc:
                rollback_errors.append(f"{target}: {exc}")
        if rollback_errors:
            raise MbError(
                "migration failed and rollback durability could not be confirmed: "
                + "; ".join(rollback_errors),
                4,
            ) from migration_error
        transaction.mark_complete(
            "rolled_back", {"transaction_id": transaction_id, "error": str(migration_error)}
        )
        raise
    transaction.mark_complete(
        "committed",
        {
            "transaction_id": transaction_id,
            "projects": [item.project_id for item in items],
        },
    )
    return {
        "transaction_id": transaction_id,
        "migrated_projects": [item.project_id for item in items],
        "source_preserved": True,
    }


def cleanup_preview(
    items: tuple[V2MigrationItem, ...], legacy_home: Path, catalog: CatalogStore
) -> dict[str, object]:
    for item in items:
        target = contained_path(catalog.vault, item.target_bank, "memory-bank")
        validate_v2_bank(target)
        if _compatible_digest(target) != _compatible_digest(Path(item.source_bank)):
            raise MbError(f"cleanup blocked by divergent target: {item.project_id}", 4)
    return {
        "mode": "cleanup-dry-run",
        "legacy_home": str(Path(legacy_home).resolve()),
        "verified_projects": [item.project_id for item in items],
        "action": "move legacy home to a local recovery backup",
    }


def apply_cleanup(
    items: tuple[V2MigrationItem, ...],
    legacy_home: Path,
    catalog: CatalogStore,
    state_root: Path,
) -> dict[str, object]:
    preview = cleanup_preview(items, legacy_home, catalog)
    raw_source = Path(legacy_home)
    if raw_source.is_symlink():
        raise MbError("refusing to clean a symlinked legacy home", 4)
    source = raw_source.resolve()
    try:
        source.relative_to(catalog.vault / "memory-bank")
    except ValueError:
        pass
    else:
        raise MbError("refusing to clean v2 memory-bank storage", 4)
    backup_root = Path(state_root) / "migration-cleanup-backups"
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = backup_root / f"legacy-{uuid4().hex[:12]}"
    shutil.move(str(source), str(target))
    return {
        **preview,
        "mode": "cleanup-applied",
        "legacy_home_removed": not source.exists(),
        "recovery_backup": str(target),
    }
