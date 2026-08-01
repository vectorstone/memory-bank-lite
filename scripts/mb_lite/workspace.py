from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from .bank import initialize_bank, validate_v2_bank
from .bindings import Binding, BindingStore
from .catalog import CatalogStore, ProjectRecord, WorkspaceRecord, generated_id, remote_project_id
from .errors import MbError
from .identity import resolve_identity
from .transaction import BackupTransaction
from .vault import contained_path


@dataclass(frozen=True)
class MemberCandidate:
    root: Path
    repository_key: str | None
    display_name: str


@dataclass(frozen=True)
class PlannedMember:
    root: Path
    project_id: str
    display_name: str
    repository_keys: tuple[str, ...]
    bank: Path
    create: bool


def discover_members(workspace_root: Path) -> tuple[Path, ...]:
    root = Path(workspace_root).resolve()
    result: list[Path] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise MbError(f"unable to scan Workspace root: {root}: {exc}", 1) from exc
    for child in children:
        if child.name.startswith(".") or not child.is_dir() or child.is_symlink():
            continue
        command = ["git", "-C", str(child), "rev-parse", "--show-toplevel"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0 and Path(completed.stdout.strip()).resolve() == child.resolve():
            result.append(child.resolve())
    return tuple(result)


def candidate_members(workspace_root: Path, explicit: tuple[Path, ...]) -> tuple[MemberCandidate, ...]:
    root = Path(workspace_root).resolve()
    paths = explicit or discover_members(root)
    unique: dict[str, MemberCandidate] = {}
    for raw in paths:
        path = raw if raw.is_absolute() else root / raw
        identity = resolve_identity(path)
        key = identity.key if identity.remote else f"local-root:{identity.root}"
        unique.setdefault(
            key,
            MemberCandidate(identity.root, identity.key if identity.remote else None, identity.slug),
        )
    return tuple(unique.values())


def register_project_root(root: Path, catalog: CatalogStore, bindings: BindingStore) -> ProjectRecord:
    identity = resolve_identity(root)
    bound = bindings.lookup_exact(identity.root)
    if identity.remote:
        existing = catalog.project_by_repository(identity.key)
        if existing is not None:
            record = existing
        else:
            project_id = remote_project_id(identity.slug, identity.key)
            record = catalog.add_project(project_id, identity.slug, (identity.key,))
    elif bound is not None:
        if bound.scope_type != "project":
            raise MbError(f"member root is bound to a {bound.scope_type}", 1)
        record = catalog.project(bound.scope_id)
    else:
        record = catalog.add_project(generated_id(identity.slug), identity.slug, ())
    bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
    initialize_bank(bank)
    validate_v2_bank(bank)
    bindings.bind(identity.root, "project", record.project_id)
    return record


def project_conversion_source(
    root: Path,
    catalog: CatalogStore,
    bindings: BindingStore,
) -> tuple[ProjectRecord, Path]:
    workspace_root = Path(root).resolve()
    binding = bindings.lookup_exact(workspace_root)
    if binding is None or binding.scope_type != "project":
        raise MbError("--convert-project requires an exact Project binding at the Workspace root", 1)
    record = catalog.project(binding.scope_id)
    bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
    validate_v2_bank(bank)
    return record, bank


def _plan_member(root: Path, catalog: CatalogStore, bindings: BindingStore) -> PlannedMember:
    identity = resolve_identity(root)
    binding = bindings.lookup_exact(identity.root)
    if binding is not None and binding.scope_type != "project":
        raise MbError(f"member root is bound to a {binding.scope_type}", 1)
    if identity.remote:
        record = catalog.project_by_repository(identity.key)
        if record is not None:
            if binding is not None and binding.scope_id != record.project_id:
                raise MbError(f"member root binding conflicts with repository identity: {identity.root}", 1)
            bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
            validate_v2_bank(bank)
            return PlannedMember(
                identity.root,
                record.project_id,
                record.display_name,
                record.repository_keys,
                bank,
                False,
            )
        if binding is not None:
            raise MbError(f"member root binding conflicts with repository identity: {identity.root}", 1)
        project_id = remote_project_id(identity.slug, identity.key)
        repository_keys = (identity.key,)
    elif binding is not None:
        record = catalog.project(binding.scope_id)
        bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
        validate_v2_bank(bank)
        return PlannedMember(
            identity.root,
            record.project_id,
            record.display_name,
            record.repository_keys,
            bank,
            False,
        )
    else:
        project_id = generated_id(identity.slug)
        repository_keys = ()
    bank = contained_path(
        catalog.vault,
        f"memory-bank/projects/{project_id}/.memory-bank",
        "memory-bank",
    )
    if bank.parent.exists() or bank.parent.is_symlink():
        raise MbError(f"unregistered member Bank path already exists: {bank.parent}", 4)
    return PlannedMember(identity.root, project_id, identity.slug, repository_keys, bank, True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir_durable(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise OSError(f"unable to find existing parent for {path}")
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise OSError(f"directory ancestor is not a real directory: {current}")
    for directory in reversed(missing):
        directory.mkdir()
        _fsync_directory(directory.parent)


def _copy_bank(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise MbError(f"Workspace Bank already exists: {target}", 4)
    source_paths = tuple(source.rglob("*"))
    if any(path.is_symlink() for path in source_paths):
        raise MbError(f"source Project Bank contains symlinks: {source}", 4)
    _mkdir_durable(target.parent)
    temporary = target.parent / f".convert-{uuid4().hex}"
    try:
        shutil.copytree(
            source,
            temporary,
            symlinks=True,
            ignore=shutil.ignore_patterns(".mb-lite.lock", "__pycache__", "*.pyc"),
        )
        if any(path.is_symlink() for path in temporary.rglob("*")):
            raise MbError(f"source Project Bank contains symlinks: {source}", 4)
        validate_v2_bank(temporary)
        os.rename(temporary, target)
        temporary = Path()
        _fsync_directory(target.parent)
    finally:
        if str(temporary) not in {"", "."} and temporary.exists():
            shutil.rmtree(temporary)


def convert_project_to_workspace(
    root: Path,
    name: str,
    member_roots: tuple[Path, ...],
    catalog: CatalogStore,
    bindings: BindingStore,
    state_root: Path,
) -> dict[str, object]:
    workspace_root = Path(root).resolve()
    source, source_bank = project_conversion_source(workspace_root, catalog, bindings)
    plans = tuple(_plan_member(path, catalog, bindings) for path in member_roots)
    if not plans:
        raise MbError("Workspace requires at least one member Project", 1)
    if any(plan.root == workspace_root or plan.project_id == source.project_id for plan in plans):
        raise MbError("Workspace root Project cannot also be a member", 1)
    member_ids = tuple(dict.fromkeys(plan.project_id for plan in plans))
    workspace_id = generated_id(name)
    workspace_bank = contained_path(
        catalog.vault,
        f"memory-bank/workspaces/{workspace_id}/.memory-bank",
        "memory-bank",
    )
    if workspace_bank.parent.exists() or workspace_bank.parent.is_symlink():
        raise MbError(f"Workspace Bank path already exists: {workspace_bank.parent}", 4)
    scope_containers = {workspace_bank.parent.parent}
    scope_containers.update(plan.bank.parent.parent for plan in plans if plan.create)
    created_containers = tuple(path for path in scope_containers if not path.exists())

    transaction_id = "workspace-convert-" + uuid4().hex[:12]
    transaction = BackupTransaction.create(Path(state_root) / "transactions", transaction_id)
    for path in (catalog.path, bindings.path):
        if path.exists() or path.is_symlink():
            transaction.capture(path, "project-workspace-conversion")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            transaction.capture_missing(path, "project-workspace-conversion")
    transaction.finish()
    created_scope_directories = [workspace_bank.parent]
    created_scope_directories.extend(plan.bank.parent for plan in plans if plan.create)
    try:
        _copy_bank(source_bank, workspace_bank)
        for plan in plans:
            if plan.create:
                _mkdir_durable(plan.bank.parent)
                record = catalog.add_project(
                    plan.project_id,
                    plan.display_name,
                    plan.repository_keys,
                )
                initialize_bank(plan.bank)
                validate_v2_bank(plan.bank)
                if record.bank_path != str(plan.bank.relative_to(catalog.vault)):
                    raise MbError(f"member Bank path changed during conversion: {plan.root}", 3)
            bindings.bind(plan.root, "project", plan.project_id)
        workspace = catalog.add_workspace(workspace_id, name, member_ids)
        bindings.rebind(
            workspace_root,
            Binding("project", source.project_id),
            Binding("workspace", workspace.workspace_id),
        )
    except Exception as conversion_error:
        rollback_errors: list[str] = []
        try:
            BackupTransaction.rollback(transaction.directory)
        except Exception as exc:
            rollback_errors.append(str(exc))
        for directory in reversed(created_scope_directories):
            try:
                if directory.is_symlink():
                    raise OSError("created scope directory became a symlink")
                if directory.exists():
                    shutil.rmtree(directory)
                    _fsync_directory(directory.parent)
            except OSError as exc:
                rollback_errors.append(f"{directory}: {exc}")
        for directory in created_containers:
            try:
                if directory.is_symlink():
                    raise OSError("created scope container became a symlink")
                if directory.exists():
                    directory.rmdir()
                    _fsync_directory(directory.parent)
            except OSError as exc:
                rollback_errors.append(f"{directory}: {exc}")
        if rollback_errors:
            raise MbError(
                "Workspace conversion failed and rollback durability could not be confirmed: "
                + "; ".join(rollback_errors),
                4,
            ) from conversion_error
        transaction.mark_complete(
            "rolled_back",
            {"transaction_id": transaction_id, "error": str(conversion_error)},
        )
        raise
    transaction.mark_complete(
        "committed",
        {
            "transaction_id": transaction_id,
            "source_project_id": source.project_id,
            "workspace_id": workspace.workspace_id,
        },
    )
    return {
        "scope_type": "workspace",
        "scope_id": workspace.workspace_id,
        "bank_path": str(workspace_bank),
        "members": list(workspace.members),
        "transaction_id": transaction_id,
        "source_project_id": source.project_id,
        "source_bank": str(source_bank),
    }


def initialize_workspace(
    root: Path,
    name: str,
    member_roots: tuple[Path, ...],
    catalog: CatalogStore,
    bindings: BindingStore,
) -> WorkspaceRecord:
    workspace_root = Path(root).resolve()
    existing = bindings.lookup_exact(workspace_root)
    if existing is not None and existing.scope_type != "workspace":
        raise MbError("Workspace root is already bound to a Project", 1)
    members = tuple(
        dict.fromkeys(register_project_root(path, catalog, bindings).project_id for path in member_roots)
    )
    if not members:
        raise MbError("Workspace requires at least one member Project", 1)
    workspace_id = existing.scope_id if existing is not None else generated_id(name)
    record = catalog.add_workspace(workspace_id, name, members)
    bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
    initialize_bank(bank)
    validate_v2_bank(bank)
    bindings.bind(workspace_root, "workspace", record.workspace_id)
    return record


def member_project_id(path: Path, catalog: CatalogStore, bindings: BindingStore) -> str:
    identity = resolve_identity(path)
    if identity.remote:
        record = catalog.project_by_repository(identity.key)
        if record is None:
            raise MbError(f"Project is not registered: {identity.key}", 2)
        return record.project_id
    binding = bindings.lookup_exact(identity.root)
    if binding is None or binding.scope_type != "project":
        raise MbError(f"Project is not registered: {identity.root}", 2)
    return binding.scope_id
