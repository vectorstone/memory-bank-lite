from dataclasses import dataclass
from pathlib import Path

from .bindings import BindingStore
from .catalog import CatalogStore, ProjectRecord, WorkspaceRecord
from .errors import MbError
from .identity import RepositoryIdentity, resolve_identity
from .vault import contained_path


@dataclass(frozen=True)
class Scope:
    scope_type: str
    scope_id: str
    display_name: str
    bank: Path
    root: Path
    repository: RepositoryIdentity | None = None
    members: tuple[str, ...] = ()


def project_scope(catalog: CatalogStore, record: ProjectRecord, root: Path, identity: RepositoryIdentity | None) -> Scope:
    return Scope(
        "project",
        record.project_id,
        record.display_name,
        contained_path(catalog.vault, record.bank_path, "memory-bank"),
        Path(root).resolve(),
        identity,
    )


def workspace_scope(catalog: CatalogStore, record: WorkspaceRecord, root: Path) -> Scope:
    return Scope(
        "workspace",
        record.workspace_id,
        record.display_name,
        contained_path(catalog.vault, record.bank_path, "memory-bank"),
        Path(root).resolve(),
        None,
        record.members,
    )


def resolve_scope(root: Path, catalog: CatalogStore, bindings: BindingStore) -> Scope:
    candidate = Path(root).resolve()
    exact = bindings.lookup_exact(candidate)
    if exact is not None and exact.scope_type == "workspace":
        return workspace_scope(catalog, catalog.workspace(exact.scope_id), candidate)
    try:
        identity = resolve_identity(candidate)
    except MbError as exc:
        if exact is None:
            raise
        if exact.scope_type == "project":
            return project_scope(catalog, catalog.project(exact.scope_id), candidate, None)
        raise exc
    if identity.remote:
        record = catalog.project_by_repository(identity.key)
        if record is None:
            raise MbError(f"project is not initialized: {identity.key}", 2)
        return project_scope(catalog, record, identity.root, identity)
    bound = bindings.lookup_exact(identity.root)
    if bound is None or bound.scope_type != "project":
        raise MbError(f"local project is not initialized: {identity.root}", 2)
    return project_scope(catalog, catalog.project(bound.scope_id), identity.root, identity)
