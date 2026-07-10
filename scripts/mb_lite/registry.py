from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
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

    @staticmethod
    def _close_descriptor(descriptor: int) -> None:
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _missing_registry(self, allow_missing: bool, exc: OSError) -> None:
        if os.path.lexists(self.path):
            raise MbError("corrupt registry: invalid registry path", 4) from exc
        if allow_missing:
            return None
        raise MbError("registry is not initialized", 2) from exc

    def _open_registry_native(
        self,
        allow_missing: bool,
        no_follow: int,
    ) -> int | None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | no_follow)
        except FileNotFoundError as exc:
            self._missing_registry(allow_missing, exc)
            return None
        except OSError as exc:
            raise MbError(f"corrupt registry: {exc}", 4) from exc
        try:
            opened_stat = os.fstat(descriptor)
        except OSError as exc:
            self._close_descriptor(descriptor)
            raise MbError(f"corrupt registry: {exc}", 4) from exc
        if not stat.S_ISREG(opened_stat.st_mode):
            self._close_descriptor(descriptor)
            raise MbError("corrupt registry: registry path is not a regular file", 4)
        return descriptor

    def _open_registry_fallback(self, allow_missing: bool) -> int | None:
        observed_registry = False
        for _ in range(2):
            try:
                registry_stat = self.path.lstat()
            except FileNotFoundError as exc:
                if observed_registry:
                    raise MbError("corrupt registry: registry path changed", 4) from exc
                self._missing_registry(allow_missing, exc)
                return None
            except OSError as exc:
                raise MbError(f"corrupt registry: {exc}", 4) from exc
            observed_registry = True
            if not stat.S_ISREG(registry_stat.st_mode):
                raise MbError(
                    "corrupt registry: registry path is not a regular file",
                    4,
                )
            descriptor: int | None = None
            try:
                descriptor = os.open(self.path, os.O_RDONLY)
                opened_stat = os.fstat(descriptor)
            except FileNotFoundError:
                if descriptor is not None:
                    self._close_descriptor(descriptor)
                continue
            except OSError as exc:
                if descriptor is not None:
                    self._close_descriptor(descriptor)
                raise MbError(f"corrupt registry: {exc}", 4) from exc
            if not stat.S_ISREG(opened_stat.st_mode):
                self._close_descriptor(descriptor)
                raise MbError(
                    "corrupt registry: registry path is not a regular file",
                    4,
                )
            if (
                opened_stat.st_dev == registry_stat.st_dev
                and opened_stat.st_ino == registry_stat.st_ino
            ):
                return descriptor
            self._close_descriptor(descriptor)
        raise MbError("corrupt registry: registry path changed", 4)

    def _load(self, allow_missing: bool) -> dict[str, object]:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if no_follow:
            descriptor = self._open_registry_native(allow_missing, no_follow)
        else:
            descriptor = self._open_registry_fallback(allow_missing)
        if descriptor is None:
            return self._empty()
        try:
            registry_file = os.fdopen(descriptor, "r", encoding="utf-8")
            descriptor = None
            with registry_file:
                data = json.load(registry_file)
        except MbError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError(f"corrupt registry: {exc}", 4) from exc
        finally:
            if descriptor is not None:
                self._close_descriptor(descriptor)
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
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.home,
                prefix=".registry.",
                suffix=".tmp",
            )
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
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(self.home, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise MbError(f"unable to write registry: {exc}", 4) from exc
        finally:
            if temporary_name is not None:
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
            raw_entry = {
                "project_id": identity.project_id,
                "remote": identity.key if identity.remote else None,
                "bank_path": str(self.bank_path(identity)),
                "known_roots": sorted(known_roots),
            }
            committed_entry = self._entry(raw_entry)
            projects[identity.key] = raw_entry
            self._write(data)
        return committed_entry

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
            raw_entry = {
                "project_id": old_entry.project_id,
                "remote": identity.key if identity.remote else None,
                "bank_path": str(old_entry.bank_path),
                "known_roots": sorted(known_roots),
            }
            committed_entry = self._entry(raw_entry)
            projects[identity.key] = raw_entry
            self._write(data)
        return committed_entry

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
        bank_path = Path(raw_bank_path)
        if not bank_path.is_absolute():
            raise MbError("corrupt registry schema", 4)
        try:
            resolved_bank_path = bank_path.resolve()
            resolved_projects_root = self.projects_root.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise MbError("corrupt registry schema", 4) from exc
        try:
            resolved_bank_path.relative_to(resolved_projects_root)
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
