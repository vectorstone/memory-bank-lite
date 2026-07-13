from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Iterator

from .errors import MbError


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def open_directory_path(path: Path) -> int:
    observed = path.lstat()
    if not stat.S_ISDIR(observed.st_mode):
        raise OSError(f"not a real directory: {path}")
    descriptor = os.open(path, _directory_flags())
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or not _same_file(observed, opened):
        os.close(descriptor)
        raise OSError(f"directory changed while opening: {path}")
    return descriptor


def open_directory_at(parent_descriptor: int, name: str) -> int:
    observed = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(observed.st_mode):
        raise OSError(f"not a real directory: {name}")
    descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or not _same_file(observed, opened):
        os.close(descriptor)
        raise OSError(f"directory changed while opening: {name}")
    return descriptor


def read_file_at(parent_descriptor: int, name: str) -> bytes:
    observed = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(observed.st_mode):
        raise OSError(f"not a regular file: {name}")
    descriptor = os.open(name, _file_flags(), dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file(observed, opened):
            raise OSError(f"file changed while opening: {name}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _opened_bank_path(bank: Path) -> Iterator[int]:
    descriptor = open_directory_path(Path(bank))
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"invalid Bank-relative path: {relative}")
    return path.parts


def read_bank_file(bank: Path, relative: str, *, required: bool = False) -> bytes | None:
    parts = _relative_parts(relative)
    try:
        with _opened_bank_path(bank) as bank_descriptor:
            descriptor = bank_descriptor
            opened: list[int] = []
            try:
                for part in parts[:-1]:
                    descriptor = open_directory_at(descriptor, part)
                    opened.append(descriptor)
                return read_file_at(descriptor, parts[-1])
            finally:
                for child_descriptor in reversed(opened):
                    os.close(child_descriptor)
    except OSError as exc:
        if required:
            raise MbError(
                f"missing readable {relative}: {Path(bank) / relative}",
                4,
            ) from exc
        return None


def read_bank_text(bank: Path, relative: str, *, required: bool = False) -> str | None:
    content = read_bank_file(bank, relative, required=required)
    if content is None:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeError as exc:
        if required:
            raise MbError(
                f"missing readable {relative}: {Path(bank) / relative}",
                4,
            ) from exc
        return None


def bank_markdown_files(bank: Path, directory: str) -> list[tuple[str, str]]:
    parts = _relative_parts(directory)
    try:
        with _opened_bank_path(bank) as bank_descriptor:
            descriptor = bank_descriptor
            opened: list[int] = []
            try:
                for part in parts:
                    descriptor = open_directory_at(descriptor, part)
                    opened.append(descriptor)
                result: list[tuple[str, str]] = []
                for name in sorted(os.listdir(descriptor)):
                    if not name.endswith(".md"):
                        continue
                    try:
                        content = read_file_at(descriptor, name).decode("utf-8")
                    except (OSError, UnicodeError):
                        continue
                    result.append((name, content))
                return result
            finally:
                for child_descriptor in reversed(opened):
                    os.close(child_descriptor)
    except OSError:
        return []


def remove_tree_at(parent_descriptor: int, name: str) -> None:
    descriptor = open_directory_at(parent_descriptor, name)
    try:
        for child in os.listdir(descriptor):
            child_stat = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(child_stat.st_mode):
                remove_tree_at(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)
