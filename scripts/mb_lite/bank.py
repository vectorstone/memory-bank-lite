import os
from pathlib import Path
import stat
from uuid import uuid4

from .bank_fs import (
    open_directory_at,
    open_directory_path,
    read_file_at,
    remove_tree_at,
)
from .errors import MbError


CORE_FILES = ("status.md", "checklist.md", "progress.md")
V2_CORE_FILES = CORE_FILES + ("attention.md",)
OPTIONAL_FILES = ("roadmap.md", "research.md", "backlog.md", "lessons.md")


def template_root() -> Path:
    return Path(__file__).resolve().parents[2] / "templates" / ".memory-bank"


def _file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _read_template(name: str) -> bytes:
    source = template_root() / name
    try:
        observed = source.lstat()
        if not stat.S_ISREG(observed.st_mode):
            raise OSError(f"not a regular template: {source}")
        descriptor = os.open(source, _file_flags())
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not _same_file(observed, opened):
                raise OSError(f"template changed while opening: {source}")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                return handle.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except OSError as exc:
        raise MbError(f"missing readable {name}: {source}", 4) from exc


def _ensure_directory(parent_descriptor: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    return open_directory_at(parent_descriptor, name)


def _write_missing_file(descriptor: int, name: str, content: bytes) -> bool:
    try:
        existing = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode):
            raise OSError(f"not a regular file: {name}")
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(name, flags, 0o600, dir_fd=descriptor)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
    return True


def _populate_bank(descriptor: int) -> None:
    notes_descriptor = _ensure_directory(descriptor, "notes")
    os.close(notes_descriptor)
    plans_descriptor = _ensure_directory(descriptor, "plans")
    try:
        done_descriptor = _ensure_directory(plans_descriptor, "done")
        os.close(done_descriptor)
    finally:
        os.close(plans_descriptor)
    for name in V2_CORE_FILES + OPTIONAL_FILES:
        _write_missing_file(descriptor, name, _read_template(name))
    for name in CORE_FILES:
        read_file_at(descriptor, name).decode("utf-8")
    os.fsync(descriptor)


def _initialize_bank_at(parent_descriptor: int, name: str) -> bool:
    try:
        existing = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISDIR(existing.st_mode):
            raise OSError(f"not a real Bank directory: {name}")
        descriptor = open_directory_at(parent_descriptor, name)
        try:
            _populate_bank(descriptor)
        finally:
            os.close(descriptor)
        return False

    temporary_name = f".mb-init-{uuid4().hex}"
    os.mkdir(temporary_name, 0o700, dir_fd=parent_descriptor)
    try:
        temporary_descriptor = open_directory_at(parent_descriptor, temporary_name)
        try:
            _populate_bank(temporary_descriptor)
        finally:
            os.close(temporary_descriptor)
        os.rename(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = ""
        os.fsync(parent_descriptor)
        return True
    finally:
        if temporary_name:
            try:
                remove_tree_at(parent_descriptor, temporary_name)
            except FileNotFoundError:
                pass


def initialize_bank(bank: Path) -> None:
    bank = Path(bank)
    try:
        bank.parent.mkdir(parents=True, exist_ok=True)
        parent_descriptor = open_directory_path(bank.parent)
        try:
            _initialize_bank_at(parent_descriptor, bank.name)
        finally:
            os.close(parent_descriptor)
    except (OSError, UnicodeError) as exc:
        raise MbError(f"missing readable status.md: {bank / 'status.md'}", 4) from exc


def initialize_project_bank(projects_root: Path, project_id: str) -> Path:
    if Path(project_id).name != project_id or project_id in {"", ".", ".."}:
        raise MbError(f"target project is outside projects_root: {project_id}", 4)
    projects_root = Path(projects_root)
    target = projects_root / project_id / ".memory-bank"
    try:
        projects_root.mkdir(parents=True, exist_ok=True)
        projects_descriptor = open_directory_path(projects_root)
        try:
            try:
                existing = os.stat(
                    project_id,
                    dir_fd=projects_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISDIR(existing.st_mode):
                    raise OSError(f"target project is not a real directory: {project_id}")
                project_descriptor = open_directory_at(projects_descriptor, project_id)
                try:
                    _initialize_bank_at(project_descriptor, ".memory-bank")
                finally:
                    os.close(project_descriptor)
                return target

            temporary_name = f".mb-project-{uuid4().hex}"
            os.mkdir(temporary_name, 0o700, dir_fd=projects_descriptor)
            try:
                temporary_descriptor = open_directory_at(
                    projects_descriptor,
                    temporary_name,
                )
                try:
                    _initialize_bank_at(temporary_descriptor, ".memory-bank")
                    os.fsync(temporary_descriptor)
                finally:
                    os.close(temporary_descriptor)
                os.rename(
                    temporary_name,
                    project_id,
                    src_dir_fd=projects_descriptor,
                    dst_dir_fd=projects_descriptor,
                )
                temporary_name = ""
                os.fsync(projects_descriptor)
                return target
            finally:
                if temporary_name:
                    try:
                        remove_tree_at(projects_descriptor, temporary_name)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(projects_descriptor)
    except (OSError, UnicodeError) as exc:
        raise MbError(f"unable to initialize Bank {target}: {exc}", 4) from exc


def validate_bank(bank: Path) -> Path:
    bank = Path(bank)
    try:
        descriptor = open_directory_path(bank)
        try:
            for name in CORE_FILES:
                try:
                    read_file_at(descriptor, name).decode("utf-8")
                except (OSError, UnicodeError) as exc:
                    raise MbError(f"missing readable {name}: {bank / name}", 4) from exc
        finally:
            os.close(descriptor)
    except MbError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MbError(f"Bank directory does not exist: {bank}", 4) from exc
    return bank


def validate_v2_bank(bank: Path) -> Path:
    validate_bank(bank)
    try:
        descriptor = open_directory_path(Path(bank))
        try:
            read_file_at(descriptor, "attention.md").decode("utf-8")
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError) as exc:
        raise MbError(f"missing readable attention.md: {Path(bank) / 'attention.md'}", 4) from exc
    return Path(bank)


def upgrade_bank_to_v2(bank: Path) -> bool:
    bank = validate_bank(Path(bank))
    try:
        descriptor = open_directory_path(bank)
        try:
            created = _write_missing_file(descriptor, "attention.md", _read_template("attention.md"))
            read_file_at(descriptor, "attention.md").decode("utf-8")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except MbError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MbError(f"unable to upgrade Bank to v2: {bank}: {exc}", 4) from exc
    validate_v2_bank(bank)
    return created
