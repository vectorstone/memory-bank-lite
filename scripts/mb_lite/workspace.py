from dataclasses import dataclass
from pathlib import Path
import subprocess

from .bank import initialize_bank, validate_v2_bank
from .bindings import BindingStore
from .catalog import CatalogStore, ProjectRecord, WorkspaceRecord, generated_id, remote_project_id
from .errors import MbError
from .identity import RepositoryIdentity, resolve_identity
from .vault import contained_path


@dataclass(frozen=True)
class MemberCandidate:
    root: Path
    repository_key: str | None
    display_name: str


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
