#!/usr/bin/env python3
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from mb_lite.bank import initialize_bank, validate_bank
from mb_lite.done import DonePayload, apply_done, bank_revision
from mb_lite.errors import MbError
from mb_lite.identity import resolve_identity
from mb_lite.recall import expand_result, search
from mb_lite.registry import RegistryStore
from mb_lite.start import build_start_context

DEFAULT_HOME = Path("/Users/wangxiaoguang/Docs/obsidian/memory-bank")
def _home() -> Path:
    return Path(os.environ.get("MB_LITE_HOME", str(DEFAULT_HOME))).expanduser()
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mb")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init"); init.add_argument("--json", action="store_true"); init.add_argument("--adopt", metavar="OLD_REPOSITORY_KEY"); init.add_argument("--confirm-adopt", action="store_true")
    done = sub.add_parser("done"); mode = done.add_mutually_exclusive_group(required=True); mode.add_argument("--revision", action="store_true"); mode.add_argument("--apply", action="store_true"); done.add_argument("--json", action="store_true")
    start = sub.add_parser("start"); start.add_argument("--json", action="store_true"); start.add_argument("--section", choices=("current", "open-items", "active-stage", "recent-progress", "notes"))
    recall = sub.add_parser("recall"); recall.add_argument("query", nargs="?"); recall.add_argument("--expand"); recall.add_argument("--json", action="store_true")
    return parser
def _bank(args, store):
    identity = resolve_identity(args.project_root)
    return identity, store.lookup(identity.key).bank_path
def main(argv=None) -> int:
    args = _parser().parse_args(argv); store = RegistryStore(_home())
    if args.action == "init":
        identity = resolve_identity(args.project_root)
        if args.adopt and not args.confirm_adopt:
            old = store.lookup(args.adopt)
            raise MbError(f"init --adopt {args.adopt} requires --confirm-adopt; reuse Bank {old.bank_path}", 1)
        if args.confirm_adopt and not args.adopt: raise MbError("--confirm-adopt requires --adopt OLD_REPOSITORY_KEY", 1)
        if args.adopt:
            old = store.lookup(args.adopt); validate_bank(old.bank_path); entry = store.adopt(identity, args.adopt)
        else:
            initialize_bank(store.bank_path(identity)); entry = store.register(identity)
        payload = {"repository_key": identity.key, "project_id": entry.project_id, "bank_path": str(entry.bank_path)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"Initialized {entry.bank_path}"); return 0
    identity, bank = _bank(args, store); validate_bank(bank)
    if args.action == "done":
        if args.revision:
            payload = {"repository_key": identity.key, "bank_path": str(bank), "bank_revision": bank_revision(bank)}
            print(json.dumps(payload, ensure_ascii=False) if args.json else payload["bank_revision"]); return 0
        raw = sys.stdin.read()
        if not raw.strip():
            raise MbError("done --apply requires a confirmed JSON payload on stdin", 1)
        result = apply_done(bank, identity.root, DonePayload.from_json(raw), datetime.now(timezone.utc))
        payload = {"bank_revision": result.revision, "note_path": result.note_path}
        print(json.dumps(payload, ensure_ascii=False) if args.json else "Memory saved."); return 0
    if args.action == "start":
        context = build_start_context(bank, args.section)
        print(json.dumps({"repository_key": identity.key, "bank_path": str(bank), "context": context}, ensure_ascii=False) if args.json else context, end="" if not args.json else "\n"); return 0
    if args.expand:
        result = expand_result(bank, args.expand); print(json.dumps(asdict(result), ensure_ascii=False) if args.json else result.snippet); return 0
    results = search(bank, args.query or "")
    if args.json: print(json.dumps([asdict(result) for result in results], ensure_ascii=False))
    else:
        for result in results: print(f"{result.result_id} | {result.path} | {result.heading} | {result.snippet}")
    return 0
if __name__ == "__main__":
    try: raise SystemExit(main())
    except MbError as exc:
        print(f"mb: {exc}", file=sys.stderr); raise SystemExit(exc.exit_code)
