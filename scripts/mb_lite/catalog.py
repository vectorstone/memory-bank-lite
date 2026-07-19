from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator, cast
from uuid import uuid4

from .errors import MbError
from .vault import contained_path


ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}-[0-9a-f]{12}")


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    display_name: str
    repository_keys: tuple[str, ...]
    bank_path: str


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    display_name: str
    members: tuple[str, ...]
    bank_path: str


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-.")
    return result[:60] or "scope"


def generated_id(name: str) -> str:
    return f"{_slug(name)}-{uuid4().hex[:12]}"


def remote_project_id(name: str, repository_key: str) -> str:
    return f"{_slug(name)}-{sha256(repository_key.encode('utf-8')).hexdigest()[:12]}"


class CatalogStore:
    def __init__(self, vault: Path, lock_root: Path | None = None) -> None:
        self.vault = Path(vault).resolve()
        self.root = self.vault / "memory-bank"
        self.path = self.root / "catalog.json"
        self.lock_path = (
            Path(lock_root) / ".catalog.lock" if lock_root is not None else self.root / ".catalog.lock"
        )

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.lock_path.is_symlink():
            raise MbError("catalog lock must not be a symlink", 4)
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise MbError(f"unable to acquire catalog lock: {exc}", 5) from exc
            yield

    @staticmethod
    def empty() -> dict[str, object]:
        return {"schema_version": 2, "projects": {}, "workspaces": {}}

    def _project(self, project_id: str, raw: object) -> ProjectRecord:
        if not ID_RE.fullmatch(project_id) or not isinstance(raw, dict) or set(raw) != {
            "display_name", "repository_keys", "bank_path"
        }:
            raise MbError("invalid catalog project schema", 4)
        display = raw.get("display_name")
        keys = raw.get("repository_keys")
        bank = raw.get("bank_path")
        if (
            not isinstance(display, str) or not display.strip()
            or not isinstance(keys, list)
            or not all(isinstance(key, str) and key for key in keys)
            or len(set(keys)) != len(keys)
            or not isinstance(bank, str)
        ):
            raise MbError("invalid catalog project schema", 4)
        expected = f"memory-bank/projects/{project_id}/.memory-bank"
        if bank != expected:
            raise MbError("invalid catalog project bank path", 4)
        contained_path(self.vault, bank, "memory-bank")
        return ProjectRecord(project_id, display.strip(), tuple(keys), bank)

    def _workspace(self, workspace_id: str, raw: object, projects: set[str]) -> WorkspaceRecord:
        if not ID_RE.fullmatch(workspace_id) or not isinstance(raw, dict) or set(raw) != {
            "display_name", "members", "bank_path"
        }:
            raise MbError("invalid catalog workspace schema", 4)
        display = raw.get("display_name")
        members = raw.get("members")
        bank = raw.get("bank_path")
        if (
            not isinstance(display, str) or not display.strip()
            or not isinstance(members, list)
            or not all(isinstance(member, str) and member in projects for member in members)
            or len(set(members)) != len(members)
            or not isinstance(bank, str)
        ):
            raise MbError("invalid catalog workspace schema", 4)
        expected = f"memory-bank/workspaces/{workspace_id}/.memory-bank"
        if bank != expected:
            raise MbError("invalid catalog workspace bank path", 4)
        contained_path(self.vault, bank, "memory-bank")
        return WorkspaceRecord(workspace_id, display.strip(), tuple(members), bank)

    def load(self, allow_missing: bool = False) -> dict[str, object]:
        try:
            if self.path.is_symlink():
                raise MbError("catalog must not be a symlink", 4)
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if allow_missing:
                return self.empty()
            raise MbError("Memory Bank Lite v2 catalog is not initialized", 2)
        except MbError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError(f"invalid catalog: {exc}", 4) from exc
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "projects", "workspaces"}:
            raise MbError("invalid catalog schema", 4)
        if raw.get("schema_version") != 2 or not isinstance(raw.get("projects"), dict) or not isinstance(raw.get("workspaces"), dict):
            raise MbError("invalid catalog schema", 4)
        projects = cast(dict[str, object], raw["projects"])
        workspaces = cast(dict[str, object], raw["workspaces"])
        aliases: dict[str, str] = {}
        for project_id, value in projects.items():
            project = self._project(project_id, value)
            for key in project.repository_keys:
                if key in aliases:
                    raise MbError(f"repository key maps to multiple projects: {key}", 4)
                aliases[key] = project_id
        for workspace_id, value in workspaces.items():
            self._workspace(workspace_id, value, set(projects))
        return raw

    def revision(self, data: dict[str, object] | None = None) -> str:
        value = self.load() if data is None else data
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _write(self, data: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Validate all entries before replacing any valid catalog.
        projects = cast(dict[str, object], data["projects"])
        for project_id, value in projects.items():
            self._project(project_id, value)
        for workspace_id, value in cast(dict[str, object], data["workspaces"]).items():
            self._workspace(workspace_id, value, set(projects))
        descriptor, temporary = tempfile.mkstemp(prefix=".catalog.", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = ""
            parent = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except OSError as exc:
            raise MbError(f"unable to write catalog: {exc}", 4) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def project_by_repository(self, key: str) -> ProjectRecord | None:
        data = self.load(allow_missing=True)
        for project_id, raw in cast(dict[str, object], data["projects"]).items():
            record = self._project(project_id, raw)
            if key in record.repository_keys:
                return record
        return None

    def project(self, project_id: str) -> ProjectRecord:
        data = self.load()
        raw = cast(dict[str, object], data["projects"]).get(project_id)
        if raw is None:
            raise MbError(f"project is not initialized: {project_id}", 2)
        return self._project(project_id, raw)

    def workspace(self, workspace_id: str) -> WorkspaceRecord:
        data = self.load()
        raw = cast(dict[str, object], data["workspaces"]).get(workspace_id)
        if raw is None:
            raise MbError(f"workspace is not initialized: {workspace_id}", 2)
        return self._workspace(workspace_id, raw, set(cast(dict[str, object], data["projects"])))

    def add_project(self, project_id: str, display_name: str, repository_keys: tuple[str, ...]) -> ProjectRecord:
        with self.locked():
            data = self.load(allow_missing=True)
            projects = cast(dict[str, object], data["projects"])
            for key in repository_keys:
                found = self.project_by_repository(key) if self.path.exists() else None
                if found is not None and found.project_id != project_id:
                    raise MbError(f"repository is already registered: {key}", 1)
            raw = projects.get(project_id)
            if raw is None:
                projects[project_id] = {
                    "display_name": display_name,
                    "repository_keys": list(repository_keys),
                    "bank_path": f"memory-bank/projects/{project_id}/.memory-bank",
                }
            else:
                current = self._project(project_id, raw)
                combined = tuple(dict.fromkeys((*current.repository_keys, *repository_keys)))
                projects[project_id] = {
                    "display_name": current.display_name,
                    "repository_keys": list(combined),
                    "bank_path": current.bank_path,
                }
            self._write(data)
            return self._project(project_id, projects[project_id])

    def add_workspace(self, workspace_id: str, display_name: str, members: tuple[str, ...]) -> WorkspaceRecord:
        with self.locked():
            data = self.load(allow_missing=True)
            projects = cast(dict[str, object], data["projects"])
            if any(member not in projects for member in members):
                raise MbError("workspace member is not initialized", 1)
            workspaces = cast(dict[str, object], data["workspaces"])
            raw = workspaces.get(workspace_id)
            if raw is None:
                workspaces[workspace_id] = {
                    "display_name": display_name,
                    "members": list(dict.fromkeys(members)),
                    "bank_path": f"memory-bank/workspaces/{workspace_id}/.memory-bank",
                }
            else:
                current = self._workspace(workspace_id, raw, set(projects))
                workspaces[workspace_id] = {
                    "display_name": current.display_name,
                    "members": list(dict.fromkeys((*current.members, *members))),
                    "bank_path": current.bank_path,
                }
            self._write(data)
            return self._workspace(workspace_id, workspaces[workspace_id], set(projects))

    def set_workspace_members(self, workspace_id: str, members: tuple[str, ...]) -> WorkspaceRecord:
        with self.locked():
            data = self.load()
            projects = cast(dict[str, object], data["projects"])
            workspaces = cast(dict[str, object], data["workspaces"])
            raw = workspaces.get(workspace_id)
            if raw is None:
                raise MbError(f"workspace is not initialized: {workspace_id}", 2)
            current = self._workspace(workspace_id, raw, set(projects))
            unique = tuple(dict.fromkeys(members))
            if any(member not in projects for member in unique):
                raise MbError("workspace member is not initialized", 1)
            workspaces[workspace_id] = {
                "display_name": current.display_name,
                "members": list(unique),
                "bank_path": current.bank_path,
            }
            self._write(data)
            return self._workspace(workspace_id, workspaces[workspace_id], set(projects))

    def projects(self) -> tuple[ProjectRecord, ...]:
        data = self.load(allow_missing=True)
        return tuple(
            self._project(project_id, raw)
            for project_id, raw in sorted(cast(dict[str, object], data["projects"]).items())
        )

    def workspaces(self) -> tuple[WorkspaceRecord, ...]:
        data = self.load(allow_missing=True)
        projects = set(cast(dict[str, object], data["projects"]))
        return tuple(
            self._workspace(workspace_id, raw, projects)
            for workspace_id, raw in sorted(cast(dict[str, object], data["workspaces"]).items())
        )
