#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, cast
from uuid import uuid4

from mb_lite.bank import validate_bank
from mb_lite.cleanup import (
    clean_hooks_json,
    manifest_owned_files,
    remove_opencode_plugin,
    strip_known_blocks,
)
from mb_lite.errors import MbError
from mb_lite.migration import MigrationPlan, plan_migration, tree_digest
from mb_lite.registry import RegistryStore
from mb_lite.transaction import BackupTransaction


DEFAULT_HOME = Path("/Users/wangxiaoguang/Docs/obsidian/memory-bank")
GLOBAL_INSTRUCTIONS = (
    ".codex/AGENTS.md",
    ".claude/CLAUDE.md",
    ".config/opencode/AGENTS.md",
    ".cursor/AGENTS.md",
    ".pi/agent/AGENTS.md",
)
GLOBAL_SHARED_FILES = GLOBAL_INSTRUCTIONS + (
    ".codex/RULES.md",
    ".codex/hooks.json",
    ".codex/settings.json",
    ".claude/AGENTS.md",
    ".claude/RULES.md",
    ".claude/hooks.json",
    ".claude/settings.json",
    ".config/opencode/RULES.md",
    ".config/opencode/hooks.json",
    ".config/opencode/opencode.json",
    ".config/opencode/settings.json",
    ".cursor/RULES.md",
    ".cursor/hooks.json",
    ".cursor/settings.json",
    ".pi/agent/RULES.md",
    ".pi/agent/hooks.json",
    ".pi/agent/settings.json",
)
PROJECT_SHARED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "RULES.md",
    "opencode.json",
    ".codex/AGENTS.md",
    ".codex/RULES.md",
    ".codex/hooks.json",
    ".codex/settings.json",
    ".claude/CLAUDE.md",
    ".claude/RULES.md",
    ".claude/hooks.json",
    ".claude/settings.json",
    ".opencode/AGENTS.md",
    ".opencode/RULES.md",
    ".opencode/hooks.json",
    ".opencode/settings.json",
    ".cursor/AGENTS.md",
    ".cursor/RULES.md",
    ".cursor/hooks.json",
    ".cursor/settings.json",
    ".pi/agent/AGENTS.md",
    ".pi/agent/RULES.md",
    ".pi/agent/hooks.json",
    ".pi/agent/settings.json",
)
PROJECT_MANIFESTS = (
    ".codex/.mb-manifest.json",
    ".opencode/.mb-manifest.json",
    ".git/mb-hooks-manifest.json",
    ".git/hooks/.memory-bank-manifest.json",
)
LEGACY_SKILLS = (
    ".claude/skills/skill-memory-bank",
    ".claude/skills/memory-bank",
    ".codex/skills/memory-bank",
    ".config/opencode/skills/memory-bank",
)


@dataclass(frozen=True)
class CleanupAction:
    kind: str
    path: Path
    detail: str
    plugin_ref: str | None = None


@dataclass(frozen=True)
class PreparedMutation:
    path: Path
    detail: str
    replacement: str | None


class MigrationArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise MbError(message, 1)


def _parser() -> argparse.ArgumentParser:
    parser = MigrationArgumentParser(prog="mb migration")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", metavar="ID")
    parser.add_argument("--timestamp")
    return parser


def _central_home() -> Path:
    return Path(os.environ.get("MB_LITE_HOME", str(DEFAULT_HOME))).expanduser()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MbError(f"corrupt {label}: {path}", 4) from exc
    if not isinstance(value, dict):
        raise MbError(f"corrupt {label}: {path}", 4)
    return value


def _shared_paths(root: Path, relatives: tuple[str, ...]) -> frozenset[Path]:
    return frozenset((root / relative).resolve() for relative in relatives)


def _anchored_path(path: Path) -> Path:
    return path.parent.resolve() / path.name


def _paths_related(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _is_cleanup_protected(
    path: Path,
    protected_paths: frozenset[Path],
    protected_roots: tuple[Path, ...],
) -> bool:
    candidates = (_anchored_path(path), path.resolve())
    protected = (*protected_paths, *protected_roots)
    return any(
        _paths_related(candidate, protected_path)
        for candidate in candidates
        for protected_path in protected
    )


def _manifest_actions(
    project: Path,
    manifest: Path,
    protected_paths: frozenset[Path],
    protected_roots: tuple[Path, ...] = (),
    dynamic_protected_paths: frozenset[Path] = frozenset(),
) -> list[CleanupAction]:
    data = _load_object(manifest, "adapter manifest")
    actions = [
        CleanupAction("remove-owned", path, f"owned by {manifest}")
        for path in manifest_owned_files(project, manifest)
        if not _is_cleanup_protected(path, protected_paths, protected_roots)
    ]
    plugin_ref = data.get("plugin_ref")
    if isinstance(plugin_ref, str) and plugin_ref:
        config = project / "opencode.json"
        if config.is_file() and not _is_cleanup_protected(
            config,
            dynamic_protected_paths,
            protected_roots,
        ):
            actions.append(
                CleanupAction(
                    "opencode-plugin",
                    config,
                    f"plugin owned by {manifest}",
                    plugin_ref,
                ),
            )
    manifest_is_owned = True
    try:
        manifest.resolve().relative_to(project.resolve())
    except (OSError, RuntimeError, ValueError):
        manifest_is_owned = False
    if manifest_is_owned and not _is_cleanup_protected(
        manifest,
        protected_paths,
        protected_roots,
    ):
        actions.append(
            CleanupAction(
                "remove-owned",
                manifest,
                "consumed legacy ownership manifest",
            ),
        )
    return actions


def _legacy_install_manifests(home: Path) -> tuple[Path, ...]:
    candidates = [home / skill / ".installed-manifest.json" for skill in LEGACY_SKILLS]
    configured = os.environ.get("MB_LEGACY_MANIFEST")
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(
            configured_path if configured_path.is_absolute() else home / configured_path,
        )
    return tuple(sorted(path for path in candidates if path.is_file()))


def _cleanup_actions(home: Path, plan: MigrationPlan) -> tuple[CleanupAction, ...]:
    actions: list[CleanupAction] = []
    global_shared = _shared_paths(home, GLOBAL_SHARED_FILES)
    retained_registries = frozenset(
        value
        for path in plan.retained_legacy_registries
        for value in (_anchored_path(path), path.resolve())
    )
    unresolved_bank_locators = frozenset(
        _anchored_path(record.bank_path)
        for record in plan.unresolved_records
        if record.bank_path is not None
    )
    dynamic_protected_paths = retained_registries | unresolved_bank_locators
    unresolved_banks = tuple(
        sorted(
            {
                record.bank_path.resolve()
                for record in plan.unresolved_records
                if record.bank_path is not None
            },
            key=str,
        ),
    )
    for relative in GLOBAL_INSTRUCTIONS:
        path = home / relative
        if path.is_file() and not _is_cleanup_protected(
            path,
            dynamic_protected_paths,
            unresolved_banks,
        ):
            actions.append(CleanupAction("strip-blocks", path, "managed prompt block"))
    claude_settings = home / ".claude/settings.json"
    if claude_settings.is_file() and not _is_cleanup_protected(
        claude_settings,
        dynamic_protected_paths,
        unresolved_banks,
    ):
        actions.append(CleanupAction("clean-hooks", claude_settings, "managed hooks"))
    for registry in plan.removable_legacy_registries:
        if not _is_cleanup_protected(
            registry,
            dynamic_protected_paths,
            unresolved_banks,
        ):
            actions.append(CleanupAction("remove-owned", registry, "legacy registry"))
    roots = sorted(
        {root for project in plan.projects.values() for root in project.known_roots},
    )
    for root in roots:
        project_shared = _shared_paths(root, PROJECT_SHARED_FILES)
        agents = root / "AGENTS.md"
        if agents.is_file() and not _is_cleanup_protected(
            agents,
            dynamic_protected_paths,
            unresolved_banks,
        ):
            actions.append(CleanupAction("strip-blocks", agents, "managed prompt block"))
        for relative in PROJECT_MANIFESTS:
            manifest = root / relative
            if manifest.is_file():
                actions.extend(
                    _manifest_actions(
                        root,
                        manifest,
                        project_shared | dynamic_protected_paths,
                        unresolved_banks,
                        dynamic_protected_paths,
                    ),
                )
    for manifest in _legacy_install_manifests(home):
        actions.extend(
            _manifest_actions(
                home,
                manifest,
                global_shared | dynamic_protected_paths,
                unresolved_banks,
                dynamic_protected_paths,
            ),
        )
    cursor_manifest = home / ".cursor/.mb-manifest.json"
    if cursor_manifest.is_file():
        actions.extend(
            _manifest_actions(
                home,
                cursor_manifest,
                global_shared | dynamic_protected_paths,
                unresolved_banks,
                dynamic_protected_paths,
            ),
        )
    deduplicated: dict[tuple[str, str, str | None], CleanupAction] = {}
    for action in actions:
        key = (action.kind, str(action.path), action.plugin_ref)
        deduplicated.setdefault(key, action)
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda action: (str(action.path), action.kind, action.plugin_ref or ""),
        ),
    )


def _render_plan(
    plan: MigrationPlan,
    central_home: Path,
    actions: tuple[CleanupAction, ...],
) -> str:
    lines = ["DRY RUN - no files will be changed", "Migration plan:"]
    for key in sorted(plan.projects):
        project = plan.projects[key]
        lines.append(f"copy-bank: {project.source_banks[0]} -> {project.target_bank}")
    lines.append(f"replace-registry: {central_home / 'registry.json'}")
    lines.extend(
        f"{action.kind}: {action.path} ({action.detail})"
        for action in actions
    )
    if plan.unresolved_records:
        lines.append("Unresolved legacy records:")
        for record in sorted(
            plan.unresolved_records,
            key=lambda value: (
                str(value.registry_path),
                str(value.root),
                str(value.bank_path),
            ),
        ):
            bank_path = record.bank_path if record.bank_path is not None else "unavailable"
            lines.append(
                "unresolved-record: "
                f"registry={record.registry_path}; "
                f"root={record.root}; "
                f"bank={bank_path}; "
                f"reason={record.reason}; registry retained",
            )
    return "\n".join(lines)


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise MbError(f"unable to rewrite {path}: {exc}", 4) from exc
    finally:
        if temporary_name is not None:
            _unlink_if_present(Path(temporary_name))


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _validate_bank_target(target: Path) -> None:
    projects_root = target.parent.parent
    if (
        target.name != ".memory-bank"
        or target.parent.name in {"", ".", ".."}
        or projects_root.is_symlink()
    ):
        raise MbError(f"target Bank escapes projects_root: {target}", 4)


def _fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _open_directory_at(parent_descriptor: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_descriptor)


def _bank_files(
    descriptor: int,
    prefix: str = "",
) -> list[tuple[str, bytes]]:
    result: list[tuple[str, bytes]] = []
    for name in sorted(os.listdir(descriptor)):
        child_stat = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        relative = f"{prefix}/{name}" if prefix else name
        if stat.S_ISDIR(child_stat.st_mode):
            child_descriptor = _open_directory_at(descriptor, name)
            try:
                result.extend(_bank_files(child_descriptor, relative))
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(child_stat.st_mode):
            if name == ".mb-lite.lock":
                continue
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            child_descriptor = os.open(name, flags, dir_fd=descriptor)
            try:
                result.append((relative, _read_descriptor(child_descriptor)))
            finally:
                os.close(child_descriptor)
        else:
            raise OSError(f"unsupported Bank entry: {relative}")
    return result


def _bank_digest_descriptor(descriptor: int) -> str:
    digest = sha256()
    for relative, content in sorted(_bank_files(descriptor)):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_bank_descriptor(descriptor: int) -> None:
    for name in ("status.md", "checklist.md", "progress.md"):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(name, flags, dir_fd=descriptor)
        try:
            _read_descriptor(file_descriptor).decode("utf-8")
        finally:
            os.close(file_descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written == 0:
            raise OSError("short write while copying Bank")
        offset += written


def _copy_directory_to_descriptor(source: Path, destination: int) -> None:
    for path in sorted(source.iterdir(), key=lambda value: value.name):
        source_stat = path.lstat()
        if stat.S_ISDIR(source_stat.st_mode):
            os.mkdir(path.name, stat.S_IMODE(source_stat.st_mode), dir_fd=destination)
            child_descriptor = _open_directory_at(destination, path.name)
            try:
                _copy_directory_to_descriptor(path, child_descriptor)
                _fsync_descriptor(child_descriptor)
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(source_stat.st_mode):
            source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            source_descriptor = os.open(path, source_flags)
            try:
                destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                destination_descriptor = os.open(
                    path.name,
                    destination_flags,
                    stat.S_IMODE(source_stat.st_mode),
                    dir_fd=destination,
                )
                try:
                    _write_all(
                        destination_descriptor,
                        _read_descriptor(source_descriptor),
                    )
                    os.fsync(destination_descriptor)
                finally:
                    os.close(destination_descriptor)
            finally:
                os.close(source_descriptor)
        else:
            raise OSError(f"unsupported Bank source entry: {path}")


def _copy_bank_to_project(source: Path, project_descriptor: int) -> None:
    os.mkdir(".memory-bank", 0o700, dir_fd=project_descriptor)
    bank_descriptor = _open_directory_at(project_descriptor, ".memory-bank")
    try:
        _copy_directory_to_descriptor(source, bank_descriptor)
        _validate_bank_descriptor(bank_descriptor)
        if _bank_digest_descriptor(bank_descriptor) != tree_digest(source):
            raise OSError("copied Bank digest mismatch")
        _fsync_descriptor(bank_descriptor)
    finally:
        os.close(bank_descriptor)


def _remove_tree_at(parent_descriptor: int, name: str) -> None:
    descriptor = _open_directory_at(parent_descriptor, name)
    try:
        for child in os.listdir(descriptor):
            child_stat = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(child_stat.st_mode):
                _remove_tree_at(descriptor, child)
            else:
                os.unlink(child, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)


def _open_projects_root(projects_root: Path, create: bool) -> int | None:
    if create:
        projects_root.mkdir(parents=True, exist_ok=True)
    elif not projects_root.exists() and not projects_root.is_symlink():
        return None
    if projects_root.is_symlink() or not projects_root.is_dir():
        raise MbError(f"projects_root is not a real directory: {projects_root}", 4)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(projects_root, flags)
    except OSError as exc:
        raise MbError(f"unable to open projects_root: {projects_root}: {exc}", 4) from exc


def _existing_bank_matches(
    target: Path,
    source_digest: str,
    projects_descriptor: int,
) -> bool:
    project_id = target.parent.name
    try:
        try:
            project_stat = os.stat(
                project_id,
                dir_fd=projects_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
    except OSError as exc:
        raise MbError(f"unable to inspect target project: {target.parent}: {exc}", 4) from exc
    if not stat.S_ISDIR(project_stat.st_mode):
        raise MbError(f"target project is not a real directory: {target.parent}", 4)
    try:
        project_descriptor = _open_directory_at(
            projects_descriptor,
            project_id,
        )
    except OSError as exc:
        raise MbError(f"unable to open target project: {target.parent}: {exc}", 4) from exc
    try:
        try:
            bank_descriptor = _open_directory_at(project_descriptor, ".memory-bank")
            try:
                _validate_bank_descriptor(bank_descriptor)
                bank_digest = _bank_digest_descriptor(bank_descriptor)
            finally:
                os.close(bank_descriptor)
        except (OSError, UnicodeError) as exc:
            raise MbError(f"target Bank is invalid: {target}", 4) from exc
        if bank_digest != source_digest:
            raise MbError(f"target Bank has different content: {target}", 4)
        return True
    finally:
        os.close(project_descriptor)


def _inspect_existing_bank(target: Path, source_digest: str) -> bool:
    _validate_bank_target(target)
    projects_descriptor = _open_projects_root(target.parent.parent, create=False)
    if projects_descriptor is None:
        return False
    try:
        return _existing_bank_matches(target, source_digest, projects_descriptor)
    finally:
        os.close(projects_descriptor)


def _copy_banks(plan: MigrationPlan) -> None:
    for key in sorted(plan.projects):
        project = plan.projects[key]
        target = project.target_bank
        _validate_bank_target(target)
        source_digest = tree_digest(project.source_banks[0])
        if _inspect_existing_bank(target, source_digest):
            continue
        projects_descriptor = _open_projects_root(target.parent.parent, create=True)
        if projects_descriptor is None:
            raise MbError(f"unable to open projects_root: {target.parent.parent}", 4)
        temporary_name = f".mb-copy-{uuid4().hex}"
        temporary_descriptor: int | None = None
        try:
            if _existing_bank_matches(target, source_digest, projects_descriptor):
                continue
            os.mkdir(temporary_name, 0o700, dir_fd=projects_descriptor)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            temporary_descriptor = os.open(
                temporary_name,
                flags,
                dir_fd=projects_descriptor,
            )
            _copy_bank_to_project(project.source_banks[0], temporary_descriptor)
            _fsync_descriptor(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = None
            os.rename(
                temporary_name,
                target.parent.name,
                src_dir_fd=projects_descriptor,
                dst_dir_fd=projects_descriptor,
            )
            temporary_name = ""
            _fsync_descriptor(projects_descriptor)
        except OSError as exc:
            raise MbError(f"unable to copy Bank to {target}: {exc}", 4) from exc
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            try:
                if temporary_name:
                    try:
                        _remove_tree_at(projects_descriptor, temporary_name)
                        _fsync_descriptor(projects_descriptor)
                    except FileNotFoundError:
                        temporary_name = ""
                    except OSError as exc:
                        raise MbError(
                            f"unable to clean temporary Bank: {temporary_name}: {exc}",
                            4,
                        ) from exc
            finally:
                os.close(projects_descriptor)


def _merged_projects(
    store: RegistryStore,
    plan: MigrationPlan,
) -> tuple[dict[str, object], dict[str, object]]:
    existing = store._load(allow_missing=True)
    expected_projects = dict(cast(dict[str, object], existing["projects"]))
    projects = dict(expected_projects)
    for key in sorted(plan.projects):
        project = plan.projects[key]
        _validate_bank_target(project.target_bank)
        source_digest = tree_digest(project.source_banks[0])
        _inspect_existing_bank(project.target_bank, source_digest)
        known_roots = {str(root) for root in project.known_roots}
        current = projects.get(key)
        if current is not None:
            current_entry = store._entry(current)
            validate_bank(current_entry.bank_path)
            if tree_digest(current_entry.bank_path) != source_digest:
                raise MbError(
                    f"existing registry Bank has different content: {current_entry.bank_path}",
                    4,
                )
            known_roots.update(current_entry.known_roots)
        projects[key] = {
            "project_id": project.identity.project_id,
            "remote": key if project.identity.remote else None,
            "bank_path": str(project.target_bank),
            "known_roots": sorted(known_roots),
        }
    return expected_projects, projects


def _capture(transaction: BackupTransaction, path: Path, action: str) -> None:
    if path.exists() or path.is_symlink():
        transaction.capture(path, action)
    else:
        transaction.capture_missing(path, action)


def _prepare_mutations(
    actions: tuple[CleanupAction, ...],
    registry_path: Path,
) -> tuple[PreparedMutation, ...]:
    grouped: dict[str, list[CleanupAction]] = {}
    for action in actions:
        if action.path.resolve() == registry_path.resolve():
            continue
        grouped.setdefault(str(action.path.resolve()), []).append(action)
    prepared: list[PreparedMutation] = []
    for resolved_path in sorted(grouped):
        path_actions = grouped[resolved_path]
        path = path_actions[0].path
        kinds = {action.kind for action in path_actions}
        detail = ", ".join(sorted({action.detail for action in path_actions}))
        if "remove-owned" in kinds:
            if len(kinds) != 1:
                raise MbError(f"conflicting cleanup actions for {path}", 4)
            if path.is_symlink() or path.is_file():
                prepared.append(PreparedMutation(path, detail, None))
            elif path.is_dir():
                raise MbError(f"refusing to remove non-symlink directory: {path}", 4)
            continue
        if kinds == {"strip-blocks"}:
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise MbError(f"unable to read instruction file: {path}", 4) from exc
            cleaned, changed = strip_known_blocks(source)
            if changed:
                prepared.append(PreparedMutation(path, detail, cleaned))
            continue
        if not kinds.issubset({"clean-hooks", "opencode-plugin"}):
            raise MbError(f"unknown cleanup action for {path}", 4)
        source = _load_object(path, "configuration")
        cleaned = source
        for action in path_actions:
            if action.kind == "clean-hooks":
                cleaned = clean_hooks_json(cleaned)
            else:
                cleaned = remove_opencode_plugin(cleaned, action.plugin_ref or "")
        if cleaned != source:
            prepared.append(
                PreparedMutation(
                    path,
                    detail,
                    json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                ),
            )
    return tuple(prepared)


def _apply_mutation(mutation: PreparedMutation) -> None:
    if mutation.replacement is not None:
        _atomic_write_text(mutation.path, mutation.replacement)
        return
    try:
        mutation.path.unlink()
    except OSError as exc:
        raise MbError(f"unable to remove owned path: {mutation.path}: {exc}", 4) from exc


def _capture_transaction(
    transaction: BackupTransaction,
    registry_path: Path,
    mutations: tuple[PreparedMutation, ...],
) -> None:
    try:
        _capture(transaction, registry_path, "replace central registry")
        for mutation in mutations:
            transaction.capture(mutation.path, mutation.detail)
        transaction.finish()
    except (OSError, MbError) as exc:
        raise MbError(f"unable to prepare migration transaction: {exc}", 4) from exc


def _apply(
    plan: MigrationPlan,
    central_home: Path,
    actions: tuple[CleanupAction, ...],
    transaction_id: str,
    state_root: Path,
) -> None:
    store = RegistryStore(central_home)
    registry_path = store.path
    expected_projects, projects = _merged_projects(store, plan)
    mutations = _prepare_mutations(actions, registry_path)
    try:
        transaction = BackupTransaction.create(state_root, transaction_id)
    except OSError as exc:
        raise MbError(f"unable to create migration transaction: {exc}", 4) from exc
    _capture_transaction(transaction, registry_path, mutations)
    _copy_banks(plan)
    try:
        store.replace_projects(
            projects,
            expected_projects=expected_projects,
        )
    except MbError as exc:
        if exc.exit_code != 3:
            _rollback_failed_apply(transaction)
        raise
    try:
        for mutation in mutations:
            _apply_mutation(mutation)
    except MbError:
        _rollback_failed_apply(transaction)
        raise
    except OSError as exc:
        _rollback_failed_apply(transaction)
        raise MbError(f"migration apply failed: {exc}", 4) from exc


def _rollback_failed_apply(transaction: BackupTransaction) -> None:
    try:
        BackupTransaction.rollback(transaction.directory)
    except (KeyError, OSError, TypeError, MbError) as exc:
        raise MbError(f"unable to roll back failed migration: {exc}", 4) from exc


def _transaction_directory(state_root: Path, transaction_id: str) -> Path:
    if (
        not transaction_id
        or transaction_id in {".", ".."}
        or Path(transaction_id).name != transaction_id
    ):
        raise MbError(f"invalid migration transaction ID: {transaction_id}", 1)
    return state_root / transaction_id


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    home = Path.home()
    state_root = home / ".local/state/mb-migration"
    if args.rollback:
        if args.timestamp:
            raise MbError("--timestamp cannot be used with --rollback", 1)
        directory = _transaction_directory(state_root, args.rollback)
        try:
            BackupTransaction.rollback(directory)
        except MbError:
            raise
        except (KeyError, OSError, TypeError) as exc:
            raise MbError(f"unable to roll back migration: {exc}", 4) from exc
        print(f"Rolled back migration {args.rollback}")
        return 0
    central_home = _central_home()
    plan = plan_migration(home, central_home)
    actions = _cleanup_actions(home, plan)
    rendered = _render_plan(plan, central_home, actions)
    if not args.apply:
        print(rendered)
        return 0
    transaction_id = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _transaction_directory(state_root, transaction_id)
    _apply(plan, central_home, actions, transaction_id, state_root)
    print(f"Migration applied successfully. Transaction ID: {transaction_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MbError as exc:
        print(f"mb migration: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
