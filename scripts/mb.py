#!/usr/bin/env python3
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from mb_lite.bank import initialize_bank, initialize_project_bank, validate_bank, validate_v2_bank
from mb_lite.bindings import BindingStore
from mb_lite.catalog import CatalogStore, generated_id, remote_project_id
from mb_lite.config import resolve_runtime, write_vault_config
from mb_lite.compound import CompoundUpdate, apply_compound_done, global_lock
from mb_lite.done import DonePayload, apply_done, bank_revision, v2_bank_revision
from mb_lite.doctor import doctor_payload, run_doctor
from mb_lite.errors import MbError
from mb_lite.frontmatter import scalar, split_frontmatter
from mb_lite.identity import resolve_identity
from mb_lite.git_evidence import collect_git_evidence
from mb_lite.recall import bank_documents, document_expand, document_search, expand_result, search
from mb_lite.registry import RegistryStore
from mb_lite.scope import resolve_scope
from mb_lite.security import enforce_safe_text
from mb_lite.publish import apply_publish_plan, build_publish_plan, locate_scope_page, publish_snapshot
from mb_lite.migrate_v2 import (
    apply_cleanup,
    apply_v2_migration,
    cleanup_preview,
    migration_preview,
    plan_v2_migration,
)
from mb_lite.start import build_start_context, build_v2_start_context, build_workspace_start_context
from mb_lite.vault import contained_path, validate_vault_contract
from mb_lite.workspace import candidate_members, initialize_workspace, member_project_id, register_project_root


DEFAULT_HOME = Path.home() / "Docs/obsidian/memory-bank"


class MbArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise MbError(message, 1)


def _legacy_home() -> Path:
    return Path(os.environ.get("MB_LITE_HOME", str(DEFAULT_HOME))).expanduser()


def _parser() -> argparse.ArgumentParser:
    parser = MbArgumentParser(prog="mb")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_action", required=True)
    set_vault = config_sub.add_parser("set-vault")
    set_vault.add_argument("path", type=Path)
    set_vault.add_argument("--json", action="store_true")

    init = sub.add_parser("init")
    init.add_argument("--json", action="store_true")
    init.add_argument("--adopt")
    init.add_argument("--confirm-adopt", action="store_true")
    init.add_argument("--workspace", action="store_true")
    init.add_argument("--name")
    init.add_argument("--member", action="append", type=Path, default=[])
    init.add_argument("--confirm-members", action="store_true")

    done = sub.add_parser("done")
    done_mode = done.add_mutually_exclusive_group(required=True)
    done_mode.add_argument("--revision", action="store_true")
    done_mode.add_argument("--apply", action="store_true")
    done.add_argument("--json", action="store_true")
    done.add_argument("--allow-sensitive", action="store_true")

    start = sub.add_parser("start")
    start.add_argument("--json", action="store_true")
    start.add_argument(
        "--section",
        choices=("current", "attention", "members", "open-items", "active-stage", "recent-progress", "notes"),
    )

    recall = sub.add_parser("recall")
    recall.add_argument("query", nargs="?")
    recall.add_argument("--expand")
    recall.add_argument("--vault", action="store_true")
    recall.add_argument("--json", action="store_true")

    workspace = sub.add_parser("workspace")
    workspace_sub = workspace.add_subparsers(dest="workspace_action", required=True)
    workspace_add = workspace_sub.add_parser("add")
    workspace_add.add_argument("member", type=Path)
    workspace_add.add_argument("--json", action="store_true")
    workspace_remove = workspace_sub.add_parser("remove")
    workspace_remove.add_argument("member", type=Path)
    workspace_remove.add_argument("--json", action="store_true")

    publish = sub.add_parser("publish")
    publish_mode = publish.add_mutually_exclusive_group(required=True)
    publish_mode.add_argument("--snapshot", action="store_true")
    publish_mode.add_argument("--preview", action="store_true")
    publish_mode.add_argument("--apply", action="store_true")
    publish.add_argument("--allow-sensitive", action="store_true")
    publish.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")

    migrate = sub.add_parser("migrate")
    migrate_mode = migrate.add_mutually_exclusive_group(required=True)
    migrate_mode.add_argument("--to-vault", action="store_true")
    migrate_mode.add_argument("--cleanup", action="store_true")
    migrate.add_argument("--legacy-home", type=Path)
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--json", action="store_true")
    return parser


def _legacy_mode() -> bool:
    return "MB_LITE_HOME" in os.environ and "MB_LITE_VAULT_PATH" not in os.environ


def _legacy_bank(args: argparse.Namespace, store: RegistryStore):
    identity = resolve_identity(args.project_root)
    return identity, store.lookup(identity.key).bank_path


def _legacy_dispatch(args: argparse.Namespace) -> int:
    store = RegistryStore(_legacy_home())
    if args.action == "init":
        if args.workspace:
            raise MbError("Workspace scopes require a v2 vault configuration", 1)
        identity = resolve_identity(args.project_root)
        if args.adopt and not args.confirm_adopt:
            old = store.lookup(args.adopt)
            raise MbError(
                f"init --adopt {args.adopt} requires --confirm-adopt; reuse Bank {old.bank_path}",
                1,
            )
        if args.confirm_adopt and not args.adopt:
            raise MbError("--confirm-adopt requires --adopt OLD_REPOSITORY_KEY", 1)
        if args.adopt:
            old = store.lookup(args.adopt)
            validate_bank(old.bank_path)
            entry = store.adopt(identity, args.adopt)
        else:
            try:
                existing = store.lookup(identity.key)
            except MbError as exc:
                if exc.exit_code != 2:
                    raise
                initialize_project_bank(store.projects_root, identity.project_id)
            else:
                validate_bank(existing.bank_path)
            entry = store.register(identity)
            validate_bank(entry.bank_path)
        payload = {
            "repository_key": identity.key,
            "project_id": entry.project_id,
            "bank_path": str(entry.bank_path),
        }
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Initialized {entry.bank_path}")
        return 0

    identity, bank = _legacy_bank(args, store)
    validate_bank(bank)
    if args.action == "done":
        if args.revision:
            payload = {
                "repository_key": identity.key,
                "bank_path": str(bank),
                "bank_revision": bank_revision(bank),
            }
            print(json.dumps(payload, ensure_ascii=False) if args.json else payload["bank_revision"])
            return 0
        raw = sys.stdin.read()
        if not raw.strip():
            raise MbError("done --apply requires a confirmed JSON payload on stdin", 1)
        result = apply_done(bank, identity.root, DonePayload.from_json(raw), datetime.now(timezone.utc))
        payload = {"bank_revision": result.revision, "note_path": result.note_path}
        print(json.dumps(payload, ensure_ascii=False) if args.json else "Memory saved.")
        return 0
    if args.action == "start":
        section = args.section
        if section in {"attention", "members"}:
            raise MbError(f"start section {section} requires v2", 1)
        context = build_start_context(bank, section)
        payload = {"repository_key": identity.key, "bank_path": str(bank), "context": context}
        print(json.dumps(payload, ensure_ascii=False) if args.json else context, end="" if not args.json else "\n")
        return 0
    if args.action == "recall":
        if args.vault:
            raise MbError("recall --vault requires v2", 1)
        if args.expand:
            result = expand_result(bank, args.expand)
            print(json.dumps(asdict(result), ensure_ascii=False) if args.json else result.snippet)
            return 0
        results = search(bank, args.query or "")
        if args.json:
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
        else:
            for result in results:
                print(f"{result.result_id} | {result.path} | {result.heading} | {result.snippet}")
        return 0
    raise MbError(f"unsupported legacy action: {args.action}", 1)


def _v2_init_project(args: argparse.Namespace, catalog: CatalogStore, bindings: BindingStore) -> dict[str, object]:
    identity = resolve_identity(args.project_root)
    bound = bindings.lookup_exact(identity.root)
    if args.confirm_adopt and not args.adopt:
        raise MbError("--confirm-adopt requires --adopt PROJECT_ID", 1)
    if bound is not None and bound.scope_type == "project" and not args.adopt:
        bound_record = catalog.project(bound.scope_id)
        if identity.remote and identity.key not in bound_record.repository_keys:
            raise MbError(
                f"repository identity changed to {identity.key}; rerun init --adopt "
                f"{bound_record.project_id} and confirm",
                1,
            )
    if args.adopt:
        record = catalog.project(args.adopt)
        if not args.confirm_adopt:
            raise MbError(
                f"init --adopt {record.project_id} requires --confirm-adopt; reuse {record.bank_path}",
                1,
            )
        project_id = record.project_id
        keys = (identity.key,) if identity.remote else ()
        record = catalog.add_project(project_id, record.display_name, keys)
    elif identity.remote:
        existing = catalog.project_by_repository(identity.key)
        if existing is None:
            project_id = remote_project_id(identity.slug, identity.key)
            record = catalog.add_project(project_id, identity.slug, (identity.key,))
        else:
            record = existing
    elif bound is not None:
        if bound.scope_type != "project":
            raise MbError(f"repository root is bound to a {bound.scope_type}", 1)
        record = catalog.project(bound.scope_id)
    else:
        project_id = generated_id(identity.slug)
        record = catalog.add_project(project_id, identity.slug, ())
    bank = contained_path(catalog.vault, record.bank_path, "memory-bank")
    initialize_bank(bank)
    validate_v2_bank(bank)
    bindings.bind(identity.root, "project", record.project_id)
    return {
        "scope_type": "project",
        "scope_id": record.project_id,
        "repository_key": identity.key if identity.remote else None,
        "bank_path": str(bank),
    }


def _v2_dispatch(args: argparse.Namespace) -> int:
    runtime = resolve_runtime()
    if args.action == "doctor":
        vault = runtime.vault
        catalog = CatalogStore(vault, runtime.state_root)
        bindings = BindingStore(runtime.state_root)
        payload = doctor_payload(run_doctor(vault, catalog, bindings, runtime.state_root))
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print("Memory Bank Lite Doctor: " + ("healthy" if payload["healthy"] else "findings require review"))
            for finding in payload["findings"]:
                print(f"[{finding['severity']}] {finding['check_id']}: {finding['message']}")
        return 0 if payload["healthy"] else 4
    vault = validate_vault_contract(runtime.vault)
    catalog = CatalogStore(vault, runtime.state_root)
    bindings = BindingStore(runtime.state_root)
    if args.action == "migrate":
        legacy_home = args.legacy_home or Path(os.environ.get("MB_LITE_HOME", str(DEFAULT_HOME))).expanduser()
        items = plan_v2_migration(legacy_home, catalog)
        if args.cleanup:
            if args.apply:
                with global_lock(runtime.state_root):
                    payload = apply_cleanup(items, legacy_home, catalog, runtime.state_root)
            else:
                payload = cleanup_preview(items, legacy_home, catalog)
        elif args.apply:
            with global_lock(runtime.state_root):
                payload = apply_v2_migration(
                    items,
                    catalog,
                    bindings,
                    runtime.state_root,
                    runtime.config_root / "config.json",
                )
        else:
            payload = migration_preview(items, legacy_home)
        print(json.dumps(payload, ensure_ascii=False) if args.json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.action == "init":
        if args.workspace:
            if not args.name or not args.name.strip():
                raise MbError("Workspace init requires --name", 1)
            candidates = candidate_members(args.project_root, tuple(args.member))
            preview = {
                "scope_type": "workspace",
                "display_name": args.name.strip(),
                "workspace_root": str(Path(args.project_root).resolve()),
                "members": [
                    {"root": str(item.root), "repository_key": item.repository_key, "display_name": item.display_name}
                    for item in candidates
                ],
                "requires_confirmation": not args.confirm_members,
            }
            if not args.confirm_members:
                print(json.dumps(preview, ensure_ascii=False) if args.json else json.dumps(preview, ensure_ascii=False, indent=2))
                return 0
            with global_lock(runtime.state_root):
                record = initialize_workspace(
                    args.project_root,
                    args.name.strip(),
                    tuple(item.root for item in candidates),
                    catalog,
                    bindings,
                )
            bank = contained_path(vault, record.bank_path, "memory-bank")
            payload = {
                "scope_type": "workspace", "scope_id": record.workspace_id,
                "bank_path": str(bank), "members": list(record.members),
            }
            print(json.dumps(payload, ensure_ascii=False) if args.json else f"Initialized {bank}")
            return 0
        with global_lock(runtime.state_root):
            payload = _v2_init_project(args, catalog, bindings)
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Initialized {payload['bank_path']}")
        return 0
    scope = resolve_scope(args.project_root, catalog, bindings)
    validate_v2_bank(scope.bank)
    if args.action == "publish":
        allowed_ids = frozenset({scope.scope_id, *scope.members})
        def publish_roots(scope_type: str, scope_id: str) -> tuple[Path, ...]:
            roots = list(bindings.roots_for_scope(scope_type, scope_id))
            if scope_type == "project" and scope.scope_type == "project" and scope_id == scope.scope_id:
                roots.append(scope.root)
            return tuple(dict.fromkeys(root.resolve() for root in roots))

        if args.snapshot:
            payload = publish_snapshot(
                vault, catalog, scope.scope_type, scope.scope_id, scope.members,
                publish_roots,
            )
            print(json.dumps(payload, ensure_ascii=False) if args.json else json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        raw = sys.stdin.read()
        if not raw.strip():
            raise MbError("publish preview/apply requires a JSON payload on stdin", 1)
        plan = build_publish_plan(
            vault,
            catalog,
            raw,
            args.allow_sensitive,
            allowed_scope_ids=allowed_ids,
            roots_for_scope=publish_roots,
        )
        if args.preview:
            payload = plan.preview(vault)
        else:
            payload = apply_publish_plan(plan, vault, runtime.state_root)
        print(json.dumps(payload, ensure_ascii=False) if args.json else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.action == "workspace":
        if scope.scope_type != "workspace":
            raise MbError("workspace member changes require a Workspace root", 1)
        current = catalog.workspace(scope.scope_id)
        with global_lock(runtime.state_root):
            if args.workspace_action == "add":
                member = register_project_root(args.member, catalog, bindings)
                updated = catalog.set_workspace_members(scope.scope_id, (*current.members, member.project_id))
            else:
                project_id = member_project_id(args.member, catalog, bindings)
                if project_id not in current.members:
                    raise MbError(f"Project is not a Workspace member: {project_id}", 1)
                updated = catalog.set_workspace_members(
                    scope.scope_id, tuple(member for member in current.members if member != project_id)
                )
        payload = {"workspace_id": updated.workspace_id, "members": list(updated.members)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Workspace members: {', '.join(updated.members)}")
        return 0
    if args.action == "done":
        if args.revision:
            if scope.scope_type == "workspace":
                closeout_path = vault / "memory-bank/closeout-state" / f"{scope.scope_id}.json"
                try:
                    closeout_state = json.loads(closeout_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    closeout_state = None
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise MbError(f"invalid Workspace closeout state: {closeout_path}", 4) from exc
                banks = [{
                    "scope_type": "workspace", "scope_id": scope.scope_id,
                    "bank_path": str(scope.bank), "bank_revision": v2_bank_revision(scope.bank),
                    "roots": [str(scope.root)], "git": [],
                }]
                for project_id in scope.members:
                    record = catalog.project(project_id)
                    bank = contained_path(vault, record.bank_path, "memory-bank")
                    roots = bindings.roots_for_scope("project", project_id)
                    banks.append({
                        "scope_type": "project", "scope_id": project_id,
                        "bank_path": str(bank), "bank_revision": v2_bank_revision(bank),
                        "roots": [str(root) for root in roots],
                        "git": [collect_git_evidence(root).json() for root in roots],
                    })
                payload = {
                    "scope_type": "workspace", "scope_id": scope.scope_id,
                    "catalog_revision": catalog.revision(), "banks": banks,
                    "closeout_state": closeout_state,
                }
            else:
                payload = {
                    "scope_type": scope.scope_type,
                    "scope_id": scope.scope_id,
                    "bank_path": str(scope.bank),
                    "bank_revision": v2_bank_revision(scope.bank),
                }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            elif scope.scope_type == "workspace":
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(payload["bank_revision"])
            return 0
        raw = sys.stdin.read()
        if not raw.strip():
            raise MbError("done --apply requires a confirmed JSON payload on stdin", 1)
        enforce_safe_text(raw, args.allow_sensitive)
        if scope.scope_type == "workspace":
            try:
                compound_raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise MbError("Workspace done payload must be valid JSON", 1) from exc
            if not isinstance(compound_raw, dict) or compound_raw.get("workspace_id") != scope.scope_id:
                raise MbError("Workspace done payload has the wrong workspace_id", 1)
            if compound_raw.get("catalog_revision") != catalog.revision():
                raise MbError("catalog changed; regenerate the Workspace done draft", 3)
            raw_updates = compound_raw.get("updates")
            if not isinstance(raw_updates, list) or not raw_updates:
                raise MbError("Workspace done payload requires updates", 1)
            allowed_ids = {scope.scope_id, *scope.members}
            updates = []
            for value in raw_updates:
                if not isinstance(value, dict) or value.get("scope_id") not in allowed_ids:
                    raise MbError("Workspace done payload contains an invalid scope", 1)
                scope_id = str(value["scope_id"])
                if scope_id == scope.scope_id:
                    bank, root = scope.bank, scope.root
                else:
                    record = catalog.project(scope_id)
                    bank = contained_path(vault, record.bank_path, "memory-bank")
                    roots = bindings.roots_for_scope("project", scope_id)
                    if not roots:
                        raise MbError(f"Project has no local checkout binding: {scope_id}", 1)
                    root = roots[0]
                updates.append(
                    CompoundUpdate(scope_id, bank, root, DonePayload.from_json(json.dumps(value, ensure_ascii=False)))
                )
            if scope.scope_id not in {update.scope_id for update in updates}:
                raise MbError("Workspace done must update the Workspace Bank", 1)
            state = {
                "schema_version": 1,
                "workspace_id": scope.scope_id,
                "members": {
                    project_id: [
                        {"branch": evidence.branch, "head": evidence.head}
                        for evidence in (
                            collect_git_evidence(root)
                            for root in bindings.roots_for_scope("project", project_id)
                        )
                    ]
                    for project_id in scope.members
                },
            }
            result = apply_compound_done(
                tuple(updates), runtime.state_root, raw,
                allow_sensitive=args.allow_sensitive, now=datetime.now(timezone.utc),
                completion_state=(
                    vault / "memory-bank/closeout-state" / f"{scope.scope_id}.json",
                    state,
                ),
            )
            payload = {
                "transaction_id": result.transaction_id,
                "revisions": result.revisions,
                "notes": result.notes,
            }
            print(json.dumps(payload, ensure_ascii=False) if args.json else f"Memory saved ({result.transaction_id}).")
            return 0
        with global_lock(runtime.state_root):
            result = apply_done(
                scope.bank,
                scope.root,
                DonePayload.from_json(raw),
                datetime.now(timezone.utc),
                revision_function=v2_bank_revision,
            )
        payload = {"bank_revision": result.revision, "note_path": result.note_path}
        print(json.dumps(payload, ensure_ascii=False) if args.json else "Memory saved.")
        return 0
    if args.action == "start":
        if args.section == "members" and scope.scope_type != "workspace":
            raise MbError("start section members requires a Workspace scope", 1)
        if scope.scope_type == "workspace":
            member_contexts = []
            for project_id in scope.members:
                record = catalog.project(project_id)
                member_bank = contained_path(vault, record.bank_path, "memory-bank")
                current = build_v2_start_context(member_bank, "current")
                focus = next(
                    (line.split("**Focus:**", 1)[1].strip() for line in current.splitlines() if "**Focus:**" in line),
                    "Current state unavailable",
                )
                blockers = next(
                    (line.split("**Blockers:**", 1)[1].strip() for line in current.splitlines() if "**Blockers:**" in line),
                    "None",
                )
                roots = bindings.roots_for_scope("project", project_id)
                evidence = collect_git_evidence(roots[0]) if roots else None
                head = evidence.head[:8] if evidence and evidence.head else "uncommitted"
                member_contexts.append(
                    (record.display_name, f"{focus}; blockers: {blockers}; HEAD: {head}")
                )
            context = build_workspace_start_context(scope.bank, tuple(member_contexts), args.section)
        else:
            context = build_v2_start_context(scope.bank, args.section)
        payload = {
            "scope_type": scope.scope_type,
            "scope_id": scope.scope_id,
            "bank_path": str(scope.bank),
            "context": context,
        }
        print(json.dumps(payload, ensure_ascii=False) if args.json else context, end="" if not args.json else "\n")
        return 0
    if args.action == "recall":
        documents = list(bank_documents(scope.bank, scope.scope_id))
        if scope.scope_type == "workspace":
            for project_id in scope.members:
                record = catalog.project(project_id)
                member_bank = contained_path(vault, record.bank_path, "memory-bank")
                documents.extend(bank_documents(member_bank, project_id))
        allowed_scope_ids = {scope.scope_id, *scope.members}
        if args.vault:
            wiki_files = sorted((vault / "wiki").rglob("*.md"))
        else:
            wiki_files = []
            for scope_id in allowed_scope_ids:
                page = locate_scope_page(vault, scope_id)
                if page:
                    wiki_files.append(vault / page)
        for path in wiki_files:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(vault).as_posix()
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError, ValueError):
                continue
            page_scope_id = "vault"
            try:
                frontmatter, _ = split_frontmatter(text)
                page_scope_id = scalar(frontmatter, "mb_scope_id") or "vault"
            except MbError:
                pass
            layer = "project-knowledge" if page_scope_id != "vault" else "vault"
            documents.append((layer, page_scope_id, relative, text))
        if args.expand:
            result = document_expand(tuple(documents), args.expand)
            print(json.dumps(asdict(result), ensure_ascii=False) if args.json else result.snippet)
            return 0
        results = document_search(tuple(documents), args.query or "")
        if args.json:
            print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
        else:
            for result in results:
                print(f"{result.result_id} | {result.path} | {result.heading} | {result.snippet}")
        return 0
    raise MbError(f"unsupported v2 action: {args.action}", 1)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "config":
        vault = validate_vault_contract(args.path)
        path = write_vault_config(vault)
        payload = {"config_path": str(path), "vault_path": str(vault)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Configured {vault}")
        return 0
    if _legacy_mode():
        return _legacy_dispatch(args)
    return _v2_dispatch(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MbError as exc:
        print(f"mb: {exc}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
