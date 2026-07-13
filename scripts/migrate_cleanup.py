#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, cast

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


def _manifest_actions(
    project: Path,
    manifest: Path,
    shared_paths: frozenset[Path],
) -> list[CleanupAction]:
    data = _load_object(manifest, "adapter manifest")
    actions = [
        CleanupAction("remove-owned", path, f"owned by {manifest}")
        for path in manifest_owned_files(project, manifest)
        if path.resolve() not in shared_paths
    ]
    plugin_ref = data.get("plugin_ref")
    if isinstance(plugin_ref, str) and plugin_ref:
        config = project / "opencode.json"
        if config.is_file():
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
    if manifest_is_owned and manifest.resolve() not in shared_paths:
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
    for relative in GLOBAL_INSTRUCTIONS:
        path = home / relative
        if path.is_file():
            actions.append(CleanupAction("strip-blocks", path, "managed prompt block"))
    claude_settings = home / ".claude/settings.json"
    if claude_settings.is_file():
        actions.append(CleanupAction("clean-hooks", claude_settings, "managed hooks"))
    for registry in plan.legacy_registries:
        actions.append(CleanupAction("remove-owned", registry, "legacy registry"))
    roots = sorted(
        {root for project in plan.projects.values() for root in project.known_roots},
    )
    for root in roots:
        project_shared = _shared_paths(root, PROJECT_SHARED_FILES)
        agents = root / "AGENTS.md"
        if agents.is_file():
            actions.append(CleanupAction("strip-blocks", agents, "managed prompt block"))
        for relative in PROJECT_MANIFESTS:
            manifest = root / relative
            if manifest.is_file():
                actions.extend(
                    _manifest_actions(root, manifest, project_shared),
                )
    for manifest in _legacy_install_manifests(home):
        actions.extend(_manifest_actions(home, manifest, global_shared))
    cursor_manifest = home / ".cursor/.mb-manifest.json"
    if cursor_manifest.is_file():
        actions.extend(_manifest_actions(home, cursor_manifest, global_shared))
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


def _copy_banks(plan: MigrationPlan) -> None:
    for key in sorted(plan.projects):
        project = plan.projects[key]
        target = project.target_bank
        if target.exists() or target.is_symlink():
            validate_bank(target)
            if tree_digest(target) != tree_digest(project.source_banks[0]):
                raise MbError(f"target Bank has different content: {target}", 4)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(project.source_banks[0], target)
        except OSError as exc:
            raise MbError(f"unable to copy Bank to {target}: {exc}", 4) from exc


def _merged_projects(store: RegistryStore, plan: MigrationPlan) -> dict[str, object]:
    existing = store._load(allow_missing=True)
    projects = dict(cast(dict[str, object], existing["projects"]))
    for key in sorted(plan.projects):
        project = plan.projects[key]
        source_digest = tree_digest(project.source_banks[0])
        if project.target_bank.exists() or project.target_bank.is_symlink():
            validate_bank(project.target_bank)
            if tree_digest(project.target_bank) != source_digest:
                raise MbError(
                    f"target Bank has different content: {project.target_bank}",
                    4,
                )
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
    return projects


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
    projects = _merged_projects(store, plan)
    mutations = _prepare_mutations(actions, registry_path)
    try:
        transaction = BackupTransaction.create(state_root, transaction_id)
    except OSError as exc:
        raise MbError(f"unable to create migration transaction: {exc}", 4) from exc
    _capture_transaction(transaction, registry_path, mutations)
    try:
        _copy_banks(plan)
        store.replace_projects(projects)
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
