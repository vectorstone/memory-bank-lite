from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from .errors import MbError


@dataclass(frozen=True)
class RollbackRecord:
    path: Path
    kind: str
    content: bytes | None = None
    target: str | None = None


def _unlink_if_present(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


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
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"path is not a real directory: {path}")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".rollback",
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        _unlink_if_present(temporary_name)


def _atomic_symlink(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".rollback",
    )
    os.close(descriptor)
    _unlink_if_present(temporary_name)
    try:
        os.symlink(target, temporary_name)
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        _unlink_if_present(temporary_name)


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _record_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("invalid record path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("record path must be absolute")
    return path


def _validate_record_parent(record: dict[str, object], path: Path) -> Path:
    raw_parent = record.get("parent")
    if not isinstance(raw_parent, str) or not raw_parent or "\0" in raw_parent:
        raise ValueError("invalid record parent")
    parent = Path(raw_parent)
    if not parent.is_absolute():
        raise ValueError("rollback destination ancestry changed")
    if path.parent.resolve() == parent:
        return path
    if (
        path.parent.exists()
        or path.parent.is_symlink()
        or parent.is_symlink()
        or not parent.is_dir()
    ):
        raise ValueError("rollback destination ancestry changed")
    return parent / path.name


def _backup_content(directory: Path, record: dict[str, object]) -> bytes:
    raw_backup = record.get("backup")
    if (
        not isinstance(raw_backup, str)
        or not raw_backup
        or "\0" in raw_backup
        or Path(raw_backup).name != raw_backup
        or raw_backup in {".", ".."}
    ):
        raise ValueError("invalid backup path")
    backup = directory / raw_backup
    if backup.is_symlink() or not backup.is_file():
        raise ValueError("invalid backup file")
    resolved_directory = directory.resolve()
    resolved_backup = backup.resolve()
    resolved_backup.relative_to(resolved_directory)
    content = backup.read_bytes()
    if not _valid_digest(record.get("sha256")):
        raise ValueError("invalid backup checksum")
    if sha256(content).hexdigest() != record["sha256"]:
        raise ValueError("backup checksum mismatch")
    return content


def _validated_records(directory: Path) -> tuple[RollbackRecord, ...]:
    manifest = directory / "manifest.json"
    checksum = directory / "manifest.sha256"
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or checksum.is_symlink()
        or not checksum.is_file()
    ):
        raise ValueError("invalid manifest file")
    manifest_content = manifest.read_bytes()
    expected_checksum = checksum.read_text(encoding="ascii").strip()
    if (
        not _valid_digest(expected_checksum)
        or sha256(manifest_content).hexdigest() != expected_checksum
    ):
        raise ValueError("manifest checksum mismatch")
    data = json.loads(manifest_content.decode("utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "records"}
        or data.get("version") != 1
        or type(data.get("version")) is not int
        or not isinstance(data.get("records"), list)
    ):
        raise ValueError("invalid manifest schema")
    result: list[RollbackRecord] = []
    seen_paths: set[Path] = set()
    for raw_record in data["records"]:
        if not isinstance(raw_record, dict):
            raise ValueError("invalid rollback record")
        kind = raw_record.get("kind")
        path = _record_path(raw_record.get("path"))
        path = _validate_record_parent(raw_record, path)
        action = raw_record.get("action")
        if not isinstance(action, str) or not action:
            raise ValueError("invalid rollback action")
        if path in seen_paths:
            raise ValueError("duplicate rollback path")
        seen_paths.add(path)
        if kind == "file":
            if set(raw_record) != {
                "path",
                "parent",
                "backup",
                "sha256",
                "action",
                "kind",
            }:
                raise ValueError("invalid file rollback record")
            result.append(
                RollbackRecord(path, kind, content=_backup_content(directory, raw_record)),
            )
        elif kind == "symlink":
            if set(raw_record) != {
                "path",
                "parent",
                "backup",
                "sha256",
                "target",
                "action",
                "kind",
            }:
                raise ValueError("invalid symlink rollback record")
            target = raw_record.get("target")
            if (
                raw_record.get("backup") is not None
                or not isinstance(target, str)
                or not target
                or "\0" in target
                or not _valid_digest(raw_record.get("sha256"))
                or sha256(target.encode("utf-8")).hexdigest() != raw_record["sha256"]
            ):
                raise ValueError("invalid symlink rollback record")
            result.append(RollbackRecord(path, kind, target=target))
        elif kind == "missing":
            if (
                set(raw_record) != {
                    "path",
                    "parent",
                    "backup",
                    "sha256",
                    "action",
                    "kind",
                }
                or raw_record.get("backup") is not None
                or raw_record.get("sha256") is not None
            ):
                raise ValueError("invalid missing rollback record")
            result.append(RollbackRecord(path, kind))
        else:
            raise ValueError("unknown rollback kind")
    return tuple(result)


@dataclass
class BackupTransaction:
    directory: Path
    records: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def create(cls, state_root: Path, timestamp: str) -> "BackupTransaction":
        _mkdir_durable(state_root)
        directory = state_root / timestamp
        directory.mkdir(exist_ok=False)
        _fsync_directory(state_root)
        return cls(directory)

    def capture(self, path: Path, action: str) -> None:
        if path.is_symlink():
            target = os.readlink(path)
            self.records.append(
                {
                    "path": str(path),
                    "parent": str(path.parent.resolve()),
                    "backup": None,
                    "sha256": sha256(target.encode("utf-8")).hexdigest(),
                    "target": target,
                    "action": action,
                    "kind": "symlink",
                },
            )
            return
        data = path.read_bytes()
        name = f"{len(self.records):04d}-{path.name}"
        backup = self.directory / name
        with backup.open("wb") as backup_file:
            backup_file.write(data)
            backup_file.flush()
            os.fsync(backup_file.fileno())
        self.records.append(
            {
                "path": str(path),
                "parent": str(path.parent.resolve()),
                "backup": name,
                "sha256": sha256(data).hexdigest(),
                "action": action,
                "kind": "file",
            },
        )

    def capture_missing(self, path: Path, action: str) -> None:
        self.records.append(
            {
                "path": str(path),
                "parent": str(path.parent.resolve()),
                "backup": None,
                "sha256": None,
                "action": action,
                "kind": "missing",
            },
        )

    def finish(self) -> None:
        content = json.dumps(
            {"version": 1, "records": self.records},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8") + b"\n"
        _atomic_write(self.directory / "manifest.json", content)
        checksum = sha256(content).hexdigest().encode("ascii") + b"\n"
        _atomic_write(self.directory / "manifest.sha256", checksum)

    @staticmethod
    def rollback(directory: Path) -> None:
        try:
            records = _validated_records(directory)
        except (
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise MbError(f"invalid rollback manifest: {directory}", 4) from exc
        try:
            for record in reversed(records):
                if record.kind == "file":
                    _atomic_write(record.path, record.content or b"")
                elif record.kind == "symlink":
                    _atomic_symlink(record.path, record.target or "")
                elif record.path.is_symlink() or record.path.is_file():
                    record.path.unlink()
                    _fsync_directory(record.path.parent)
                elif record.path.exists():
                    raise OSError(f"rollback path is not a file: {record.path}")
        except (OSError, ValueError) as exc:
            raise MbError(f"unable to restore rollback manifest: {directory}", 4) from exc
