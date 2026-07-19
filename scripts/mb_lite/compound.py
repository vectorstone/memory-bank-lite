from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .bank import validate_v2_bank
from .done import BankWriteError, DonePayload, _bank_lock, _write_all_or_rollback, prepare_done, v2_bank_revision
from .errors import MbError
from .security import enforce_safe_text
from .transaction import BackupTransaction


@dataclass(frozen=True)
class CompoundUpdate:
    scope_id: str
    bank: Path
    root: Path
    payload: DonePayload


@dataclass(frozen=True)
class CompoundResult:
    transaction_id: str
    revisions: dict[str, str]
    notes: dict[str, str | None]


@contextmanager
def global_lock(state_root: Path) -> Iterator[None]:
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / ".global.lock"
    if path.is_symlink():
        raise MbError("global lock must not be a symlink", 4)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise MbError(f"unable to acquire global lock: {exc}", 5) from exc
        transactions = root / "transactions"
        if transactions.is_dir():
            incomplete = [
                path.name
                for path in transactions.iterdir()
                if path.is_dir() and not (path / "complete.json").is_file()
            ]
            if incomplete:
                raise MbError(
                    "incomplete transaction blocks writes; run /mb doctor and review: "
                    + ", ".join(sorted(incomplete)),
                    4,
                )
        yield


def apply_compound_done(
    updates: tuple[CompoundUpdate, ...],
    state_root: Path,
    raw_payload: str,
    *,
    allow_sensitive: bool = False,
    now: datetime | None = None,
    completion_state: tuple[Path, dict[str, object]] | None = None,
) -> CompoundResult:
    if not updates:
        raise MbError("compound done requires at least one Bank update", 1)
    if len({update.scope_id for update in updates}) != len(updates):
        raise MbError("compound done contains duplicate scope IDs", 1)
    enforce_safe_text(raw_payload, allow_sensitive)
    timestamp = now or datetime.now(timezone.utc)
    ordered = tuple(sorted(updates, key=lambda update: update.scope_id))
    with global_lock(state_root), ExitStack() as stack:
        for update in ordered:
            validate_v2_bank(update.bank)
            stack.enter_context(_bank_lock(update.bank))
        for update in ordered:
            if v2_bank_revision(update.bank) != update.payload.bank_revision:
                raise MbError(f"Bank revision changed for {update.scope_id}; regenerate the done draft", 3)
        prepared = [(update, prepare_done(update.bank, update.root, update.payload, timestamp)) for update in ordered]
        transaction_id = timestamp.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        outputs = tuple(output for _, item in prepared for output in item.outputs)
        if completion_state is not None:
            state_path, state_payload = completion_state
            state_content = json.dumps(
                {**state_payload, "transaction_id": transaction_id},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n"
            outputs = (*outputs, (state_path, state_content))
        transaction = BackupTransaction.create(Path(state_root) / "transactions", transaction_id)
        for path, _ in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() or path.is_symlink():
                transaction.capture(path, "compound-done")
            else:
                transaction.capture_missing(path, "compound-done")
        transaction.finish()
        try:
            _write_all_or_rollback(outputs)
        except BankWriteError as exc:
            if exc.rollback_complete:
                transaction.mark_complete(
                    "rolled_back",
                    {"transaction_id": transaction_id, "error": str(exc)},
                )
            raise
        transaction.mark_complete(
            "committed",
            {
                "transaction_id": transaction_id,
                "output_hashes": {
                    str(path): sha256(path.read_bytes()).hexdigest() for path, _ in outputs
                },
            },
        )
        revisions = {update.scope_id: v2_bank_revision(update.bank) for update in ordered}
        notes = {update.scope_id: item.note_path for update, item in prepared}
        return CompoundResult(transaction_id, revisions, notes)
