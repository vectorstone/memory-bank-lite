from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .attention import parse_attention
from .bank import validate_v2_bank
from .bank_fs import read_bank_text
from .bindings import BindingStore
from .catalog import CatalogStore
from .errors import MbError
from .publish import locate_scope_page
from .security import scan_text
from .transport import VaultTransport
from .vault import contained_path, validate_vault_contract


@dataclass(frozen=True)
class DoctorFinding:
    check_id: str
    severity: str
    message: str
    path: str | None = None


def run_doctor(
    vault: Path,
    catalog: CatalogStore,
    bindings: BindingStore,
    state_root: Path,
) -> tuple[DoctorFinding, ...]:
    findings: list[DoctorFinding] = []
    try:
        validate_vault_contract(vault)
    except MbError as exc:
        findings.append(DoctorFinding("vault-contract", "error", str(exc), str(vault)))
        return tuple(findings)
    try:
        selected = VaultTransport.load(vault).selected()
        findings.append(DoctorFinding("transport", "info", f"selected transport: {selected}"))
    except MbError as exc:
        findings.append(DoctorFinding("transport", "error", str(exc)))
    try:
        catalog.load()
        projects = catalog.projects()
        workspaces = catalog.workspaces()
    except MbError as exc:
        findings.append(DoctorFinding("catalog-invalid", "error", str(exc), str(catalog.path)))
        projects = ()
        workspaces = ()
    try:
        bindings.load()
        bindings_valid = True
    except MbError as exc:
        findings.append(DoctorFinding("bindings-invalid", "error", str(exc), str(bindings.path)))
        bindings_valid = False
    for record in projects:
        bank = contained_path(vault, record.bank_path, "memory-bank")
        try:
            validate_v2_bank(bank)
            attention_source = read_bank_text(bank, "attention.md", required=True) or ""
            for entry in parse_attention(attention_source):
                if entry.status == "active" and entry.review_due(date.today()):
                    findings.append(
                        DoctorFinding(
                            "attention-review-due",
                            "warning",
                            f"{entry.attention_id} requires review",
                            f"{record.bank_path}/attention.md",
                        )
                    )
            for path in bank.rglob("*.md"):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if scan_text(text):
                    findings.append(
                        DoctorFinding(
                            "sensitive-content", "error", "high-confidence sensitive content", str(path)
                        )
                    )
        except MbError as exc:
            findings.append(DoctorFinding("bank-invalid", "error", str(exc), record.bank_path))
        roots = bindings.roots_for_scope("project", record.project_id) if bindings_valid else ()
        if not roots:
            findings.append(
                DoctorFinding("binding-missing", "warning", "Project has no local checkout", record.project_id)
            )
        for root in roots:
            if not root.is_dir():
                findings.append(
                    DoctorFinding("binding-stale", "warning", "bound Project root is missing", str(root))
                )
    for record in workspaces:
        bank = contained_path(vault, record.bank_path, "memory-bank")
        try:
            validate_v2_bank(bank)
        except MbError as exc:
            findings.append(DoctorFinding("bank-invalid", "error", str(exc), record.bank_path))
        if bindings_valid and not bindings.roots_for_scope("workspace", record.workspace_id):
            findings.append(
                DoctorFinding("binding-missing", "warning", "Workspace has no local root", record.workspace_id)
            )
    typed_ids = [
        *(("project", record.project_id) for record in projects),
        *(("workspace", record.workspace_id) for record in workspaces),
    ]
    for scope_type, scope_id in typed_ids:
        try:
            page = locate_scope_page(vault, scope_id)
        except MbError as exc:
            findings.append(DoctorFinding("publish-page-duplicate", "error", str(exc), scope_id))
            continue
        state_path = vault / "memory-bank/publish-state" / f"{scope_id}.json"
        if page and state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                text = (vault / page).read_text(encoding="utf-8")
                start_marker = f"<!-- mb-lite:{scope_type}-knowledge:start -->"
                end_marker = f"<!-- mb-lite:{scope_type}-knowledge:end -->"
                start = text.index(start_marker)
                end = text.index(end_marker, start) + len(end_marker)
                observed = sha256(text[start:end].encode("utf-8")).hexdigest()
                if state.get("managed_hash") != observed:
                    findings.append(
                        DoctorFinding(
                            "publish-managed-drift", "error", "managed block differs from publish state", page
                        )
                    )
                if len(text.splitlines()) > 300:
                    findings.append(
                        DoctorFinding("publish-page-large", "warning", "knowledge page exceeds 300 lines", page)
                    )
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError, AttributeError) as exc:
                findings.append(
                    DoctorFinding("publish-state-invalid", "error", str(exc), str(state_path))
                )
        elif page and not state_path.exists():
            findings.append(
                DoctorFinding("publish-state-missing", "warning", "knowledge page has no publish state", page)
            )
    transaction_root = Path(state_root) / "transactions"
    if transaction_root.is_dir():
        for directory in sorted(transaction_root.iterdir()):
            if directory.is_dir() and not (directory / "complete.json").is_file():
                findings.append(
                    DoctorFinding(
                        "transaction-incomplete", "error", "incomplete transaction requires review", str(directory)
                    )
                )
    return tuple(findings)


def doctor_payload(findings: tuple[DoctorFinding, ...]) -> dict[str, object]:
    return {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "healthy": not any(item.severity == "error" for item in findings),
        "findings": [asdict(item) for item in findings],
    }
