from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import cast

from .errors import MbError
from .identity import RepositoryIdentity


@dataclass(frozen=True)
class RegistryEntry:
    project_id: str
    remote: str | None
    bank_path: Path
    known_roots: tuple[str, ...]


class RegistryStore:
    def __init__(self, home: Path) -> None:
        self.home = home.resolve()
        self.projects_root = self.home / "projects"
        self.path = self.home / "registry.json"
        self.lock_path = self.home / ".registry.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            lock_file = self.lock_path.open("a+")
        except OSError as exc:
            raise MbError(f"unable to acquire registry lock: {exc}", 5) from exc
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            lock_file.close()
            raise MbError(f"unable to acquire registry lock: {exc}", 5) from exc
        try:
            yield
        finally:
            lock_file.close()

    def _empty(self) -> dict[str, object]:
        return {
            "version": 1,
            "projects_root": str(self.projects_root),
            "projects": {},
        }

    def _load(self, allow_missing: bool) -> dict[str, object]:
        try:
            with self.path.open(encoding="utf-8") as registry_file:
                data = json.load(registry_file)
        except FileNotFoundError as exc:
            if allow_missing:
                return self._empty()
            raise MbError("registry is not initialized", 2) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError(f"corrupt registry: {exc}", 4) from exc
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "projects_root", "projects"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["projects_root"], str)
            or data["projects_root"] != str(self.projects_root)
            or not isinstance(data.get("projects"), dict)
        ):
            raise MbError("corrupt registry schema", 4)
        projects = cast(dict[str, object], data["projects"])
        for raw_entry in projects.values():
            self._entry(raw_entry)
        return data

    def _write(self, data: dict[str, object]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.home,
            prefix=".registry.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(
                    data,
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def bank_path(self, identity: RepositoryIdentity) -> Path:
        return self.projects_root / identity.project_id / ".memory-bank"

    def register(self, identity: RepositoryIdentity) -> RegistryEntry:
        with self.locked():
            data = self._load(allow_missing=True)
            projects = cast(dict[str, object], data["projects"])
            current = projects.get(identity.key)
            known_roots = (
                set(self._entry(current).known_roots)
                if current is not None
                else set()
            )
            known_roots.add(str(identity.root))
            projects[identity.key] = {
                "project_id": identity.project_id,
                "remote": identity.key if identity.remote else None,
                "bank_path": str(self.bank_path(identity)),
                "known_roots": sorted(known_roots),
            }
            self._write(data)
        return self.lookup(identity.key)

    def lookup(self, key: str) -> RegistryEntry:
        data = self._load(allow_missing=False)
        projects = cast(dict[str, object], data["projects"])
        raw_entry = projects.get(key)
        if raw_entry is None:
            raise MbError(f"project is not initialized: {key}", 2)
        return self._entry(raw_entry)

    def adopt(self, identity: RepositoryIdentity, old_key: str) -> RegistryEntry:
        with self.locked():
            data = self._load(allow_missing=False)
            projects = cast(dict[str, object], data["projects"])
            raw_old_entry = projects.get(old_key)
            if not isinstance(raw_old_entry, dict):
                raise MbError(f"project is not initialized: {old_key}", 2)
            old_entry = self._entry(raw_old_entry)
            known_roots = set(old_entry.known_roots)
            known_roots.add(str(identity.root))
            projects[identity.key] = {
                "project_id": old_entry.project_id,
                "remote": identity.key if identity.remote else None,
                "bank_path": str(old_entry.bank_path),
                "known_roots": sorted(known_roots),
            }
            self._write(data)
        return self.lookup(identity.key)

    def _entry(self, raw_entry: object) -> RegistryEntry:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "project_id",
            "remote",
            "bank_path",
            "known_roots",
        }:
            raise MbError("corrupt registry schema", 4)
        project_id = raw_entry.get("project_id")
        remote = raw_entry.get("remote")
        raw_bank_path = raw_entry.get("bank_path")
        known_roots = raw_entry.get("known_roots")
        if (
            not isinstance(project_id, str)
            or not project_id
            or (
                remote is not None
                and (not isinstance(remote, str) or not remote)
            )
            or not isinstance(raw_bank_path, str)
            or not raw_bank_path
            or not isinstance(known_roots, list)
            or not all(isinstance(root, str) and root != "" for root in known_roots)
        ):
            raise MbError("corrupt registry schema", 4)
        try:
            resolved_bank_path = Path(raw_bank_path).resolve()
            resolved_bank_path.relative_to(self.projects_root.resolve())
        except (OSError, RuntimeError) as exc:
            raise MbError("corrupt registry schema", 4) from exc
        except ValueError as exc:
            raise MbError(
                f"bank path is outside projects_root: {resolved_bank_path}",
                4,
            ) from exc
        return RegistryEntry(
            project_id=project_id,
            remote=remote,
            bank_path=resolved_bank_path,
            known_roots=tuple(known_roots),
        )
