from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import difflib
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Iterator
from uuid import uuid4

from .catalog import CatalogStore
from .compound import global_lock
from .done import v2_bank_revision
from .errors import MbError
from .frontmatter import patch_scalars, scalar, split_frontmatter
from .git_evidence import collect_git_evidence
from .security import enforce_safe_text
from .transaction import BackupTransaction
from .transport import VaultTransport
from .vault import contained_path


PROJECT_START = "<!-- mb-lite:project-knowledge:start -->"
PROJECT_END = "<!-- mb-lite:project-knowledge:end -->"
WORKSPACE_START = "<!-- mb-lite:workspace-knowledge:start -->"
WORKSPACE_END = "<!-- mb-lite:workspace-knowledge:end -->"
REQUIRED_HEADINGS = (
    "Purpose And Boundary",
    "Current Capability Boundary",
    "Architecture And Key Paths",
    "Stable Decisions And Rationale",
    "Invariants And Attention",
    "Verified Lessons",
    "Superseded Knowledge",
    "Long-Term Open Questions",
)
SHARED = ("wiki/index.md", "wiki/sources/_index.md", "wiki/log.md", "wiki/hot.md")


def content_hash(content: bytes | None) -> str | None:
    return sha256(content).hexdigest() if content is not None else None


def _slug(value: str) -> str:
    result = re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE).strip("-")
    return result or "project"


def _managed(source: str, scope_type: str) -> tuple[int, int, str]:
    start_marker, end_marker = (
        (PROJECT_START, PROJECT_END) if scope_type == "project" else (WORKSPACE_START, WORKSPACE_END)
    )
    if source.count(start_marker) != 1 or source.count(end_marker) != 1:
        raise MbError(f"knowledge page must contain exactly one {scope_type} managed block", 4)
    start = source.index(start_marker)
    end_start = source.index(end_marker, start)
    end = end_start + len(end_marker)
    return start, end, source[start:end]


def _validate_managed(value: str) -> None:
    if not value.strip():
        raise MbError("publish managed_markdown must not be empty", 1)
    for heading in REQUIRED_HEADINGS:
        if len(re.findall(rf"(?m)^## {re.escape(heading)}\s*$", value)) != 1:
            raise MbError(f"publish knowledge must contain exactly one heading: {heading}", 1)
    forbidden = ("Current Tasks", "Next Steps", "Blockers", "Recent Progress")
    for heading in forbidden:
        if re.search(rf"(?mi)^##+\s+{re.escape(heading)}\s*$", value):
            raise MbError(f"operational section is not publishable: {heading}", 1)


def _new_page(scope_type: str, scope_id: str, title: str, managed: str, bank_path: str, repository_key: str | None) -> str:
    marker_start, marker_end = (
        (PROJECT_START, PROJECT_END) if scope_type == "project" else (WORKSPACE_START, WORKSPACE_END)
    )
    source_type = "repository" if scope_type == "project" else "development-workspace"
    today = date.today().isoformat()
    sources = f'  - "[[{bank_path}/status.md]]"\n'
    if repository_key:
        sources += f'  - "https://{repository_key}"\n'
    quoted_title = json.dumps(title, ensure_ascii=False)
    return (
        "---\n"
        f"type: source\ntitle: {quoted_title}\ncreated: {today}\nupdated: {today}\n"
        "tags:\n  - project-knowledge\n  - memory-bank-lite\nstatus: developing\n"
        f"source_type: {source_type}\nmb_scope: {scope_type}\nmb_scope_id: \"{scope_id}\"\n"
        f"mb_managed_hash: \"{sha256((marker_start + chr(10) + managed.strip() + chr(10) + marker_end).encode()).hexdigest()}\"\n"
        f"sources:\n{sources}---\n\n# {title}\n\n"
        f"{marker_start}\n{managed.strip()}\n{marker_end}\n"
    )


def _replace_page(source: str, scope_type: str, scope_id: str, managed: str) -> tuple[str, str]:
    start, end, old = _managed(source, scope_type)
    marker_start, marker_end = (
        (PROJECT_START, PROJECT_END) if scope_type == "project" else (WORKSPACE_START, WORKSPACE_END)
    )
    block = f"{marker_start}\n{managed.strip()}\n{marker_end}"
    updated = source[:start] + block + source[end:]
    updated = patch_scalars(
        updated,
        {
            "updated": date.today().isoformat(),
            "mb_scope": scope_type,
            "mb_scope_id": scope_id,
            "mb_managed_hash": sha256(block.encode("utf-8")).hexdigest(),
        },
    )
    return updated, old


def locate_scope_page(vault: Path, scope_id: str) -> str | None:
    matches: list[str] = []
    root = vault / "wiki"
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, MbError):
            continue
        if scalar(frontmatter, "mb_scope_id") == scope_id:
            matches.append(path.relative_to(vault).as_posix())
    if len(matches) > 1:
        raise MbError(f"duplicate mb_scope_id pages: {scope_id}", 4)
    return matches[0] if matches else None


def _patch_updated(source: str) -> str:
    if source.startswith("---\n") and "\n---\n" not in source:
        first_heading = re.search(r"(?m)^#\s+", source)
        if first_heading is None:
            raise MbError("wiki page has unterminated YAML frontmatter", 4)
        header = source[:first_heading.start()]
        pattern = re.compile(r"(?m)^updated:\s*.*$")
        matches = list(pattern.finditer(header))
        if len(matches) != 1:
            raise MbError("legacy wiki header must contain exactly one updated field", 4)
        rendered = f'updated: "{date.today().isoformat()}"'
        match = matches[0]
        return source[:match.start()] + rendered + source[match.end():]
    return patch_scalars(source, {"updated": date.today().isoformat()})


def _upsert_index(
    source: str,
    scope_id: str,
    title: str,
    summary: str,
    page_path: str,
    sources_index: bool,
) -> str:
    source = _patch_updated(source)
    marker = f"<!-- mb-lite-source:{scope_id} -->"
    target = page_path.removesuffix(".md")
    entry = f"{marker}\n- [[{target}|{title}]] - {summary}"
    pattern = re.compile(re.escape(marker) + r"\n- .*?(?=\n(?:<!--|##|###)|\Z)", re.DOTALL)
    matches = list(pattern.finditer(source))
    if len(matches) > 1:
        raise MbError(f"duplicate index marker for {scope_id}", 4)
    if matches:
        return source[:matches[0].start()] + entry + source[matches[0].end():]
    heading = "## By Type" if sources_index else "## Sources"
    match = re.search(rf"(?m)^{re.escape(heading)}\s*$", source)
    if match is None:
        raise MbError(f"wiki index is missing {heading}", 4)
    position = match.end()
    return source[:position] + "\n" + entry + source[position:]


def _prepend_log(source: str, title: str, paths: tuple[str, ...], transaction_id: str) -> str:
    source = _patch_updated(source)
    heading = re.search(r"(?m)^# Wiki Log\s*$", source)
    if heading is None:
        raise MbError("wiki/log.md is missing its H1", 4)
    entry = (
        f"\n\n## [{date.today().isoformat()}] publish | {title}\n"
        f"- Transaction: `{transaction_id}`\n"
        f"- Pages updated: {', '.join(f'`{path}`' for path in paths)}\n"
        "- Source: explicit Memory Bank Lite publish\n"
    )
    return source[:heading.end()] + entry + source[heading.end():]


def _read_state(vault: Path, scope_id: str) -> dict[str, object] | None:
    path = vault / "memory-bank/publish-state" / f"{scope_id}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MbError(f"invalid publish state for {scope_id}", 4) from exc
    if not isinstance(value, dict):
        raise MbError(f"invalid publish state for {scope_id}", 4)
    return value


def publish_snapshot(
    vault: Path,
    catalog: CatalogStore,
    scope_type: str,
    scope_id: str,
    member_ids: tuple[str, ...],
    roots_for_scope,
) -> dict[str, object]:
    mode = subprocess.run(
        ["python3", str(vault / "scripts/wiki-mode.py"), "get"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=False,
    )
    if mode.returncode != 0 or mode.stdout.strip() != "generic":
        raise MbError("the initial publish adapter requires claude-obsidian generic mode", 4)
    route = subprocess.run(
        ["python3", str(vault / "scripts/wiki-mode.py"), "route", "source", "mb-publish-probe"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=False,
    )
    if route.returncode != 0 or not route.stdout.strip().startswith("wiki/sources/"):
        raise MbError("claude-obsidian source routing must stay under wiki/sources/", 4)
    allowed = (scope_id, *member_ids) if scope_type == "workspace" else (scope_id,)
    scopes = []
    expected = {}
    for item_id in allowed:
        if item_id == scope_id and scope_type == "workspace":
            record = catalog.workspace(item_id)
            item_type = "workspace"
            repository_key = None
        else:
            record = catalog.project(item_id)
            item_type = "project"
            repository_key = record.repository_keys[0] if record.repository_keys else None
        bank = contained_path(vault, record.bank_path, "memory-bank")
        page = locate_scope_page(vault, item_id)
        state_path = f"memory-bank/publish-state/{item_id}.json"
        state_bytes = (vault / state_path).read_bytes() if (vault / state_path).exists() else None
        expected[state_path] = content_hash(state_bytes)
        if page:
            expected[page] = content_hash((vault / page).read_bytes())
        git = [collect_git_evidence(root).json() for root in roots_for_scope(item_type, item_id)]
        scopes.append(
            {
                "scope_type": item_type,
                "scope_id": item_id,
                "display_name": record.display_name,
                "bank_path": record.bank_path,
                "bank_revision": v2_bank_revision(bank),
                "repository_key": repository_key,
                "page_path": page,
                "page_hash": content_hash((vault / page).read_bytes()) if page else None,
                "publish_state": _read_state(vault, item_id),
                "publish_state_hash": content_hash(state_bytes),
                "git": git,
                "git_cursors": [
                    {"branch": entry.get("branch", ""), "head": entry["head"]}
                    for entry in git
                    if entry.get("head") is not None
                ],
            }
        )
    for relative in SHARED:
        expected[relative] = content_hash((vault / relative).read_bytes())
    manifest_path = vault / ".raw/.manifest.json"
    if manifest_path.is_file():
        expected[".raw/.manifest.json"] = content_hash(manifest_path.read_bytes())
    proposals = iter(_address_proposals(vault, sum(1 for item in scopes if item["page_path"] is None)))
    for item in scopes:
        item["address"] = next(proposals, None) if item["page_path"] is None else None
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "catalog_revision": catalog.revision(),
        "scopes": scopes,
        "expected_hashes": expected,
        "transaction_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
    }


@dataclass(frozen=True)
class PublishPlan:
    transaction_id: str
    outputs: dict[str, bytes]
    expected_hashes: dict[str, str | None]
    changed_scopes: tuple[str, ...]
    address_reservations: tuple[tuple[str, str], ...] = ()

    def preview(self, vault: Path) -> dict[str, object]:
        diffs = {}
        warnings = []
        for relative, content in self.outputs.items():
            path = vault / relative
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            new = content.decode("utf-8")
            diffs[relative] = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True), new.splitlines(keepends=True),
                    fromfile=relative, tofile=relative,
                )
            )
            if relative.startswith("wiki/sources/") and relative != "wiki/sources/_index.md" and len(new.splitlines()) > 300:
                warnings.append(f"{relative} exceeds 300 lines; consider an explicit later split")
        return {
            "transaction_id": self.transaction_id,
            "changed_scopes": list(self.changed_scopes),
            "files": list(self.outputs),
            "diffs": diffs,
            "noop": not self.outputs,
            "address_reservations": dict(self.address_reservations),
            "warnings": warnings,
        }


def _address_proposals(vault: Path, count: int) -> tuple[str, ...]:
    script = vault / "scripts/allocate-address.sh"
    if count == 0 or not script.is_file():
        return ()
    result = subprocess.run(
        ["bash", str(script), "--peek"], cwd=vault, capture_output=True, text=True, check=False
    )
    value = result.stdout.strip()
    if result.returncode != 0:
        # Addresses are an optional retrieval optimization in claude-obsidian.
        # A vault may ship the allocator on a host without its lock dependency.
        return ()
    if re.fullmatch(r"[0-9]+", value):
        start = int(value)
    elif re.fullmatch(r"c-[0-9]{6}", value):
        start = int(value.split("-")[1])
    else:
        raise MbError("unable to preview claude-obsidian address allocation", 4)
    return tuple(f"c-{start + offset:06d}" for offset in range(count))


def build_publish_plan(
    vault: Path,
    catalog: CatalogStore,
    raw_payload: str,
    allow_sensitive: bool = False,
    allowed_scope_ids: frozenset[str] | None = None,
    roots_for_scope=None,
) -> PublishPlan:
    enforce_safe_text(raw_payload, allow_sensitive)
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise MbError("publish payload must be valid JSON", 1) from exc
    if not isinstance(payload, dict) or payload.get("catalog_revision") != catalog.revision():
        raise MbError("catalog changed; regenerate the publish snapshot", 3)
    expected = payload.get("expected_hashes")
    updates = payload.get("updates")
    hot_content = payload.get("hot_content")
    if not isinstance(expected, dict) or not isinstance(updates, list) or not isinstance(hot_content, str):
        raise MbError("invalid publish payload", 1)
    expected_hashes: dict[str, str | None] = {}
    for relative, digest in expected.items():
        if not isinstance(relative, str) or (digest is not None and not isinstance(digest, str)):
            raise MbError("invalid publish expected_hashes", 1)
        expected_hashes[relative] = digest
    outputs: dict[str, bytes] = {}
    changed_scopes: list[str] = []
    changed_entries: list[tuple[dict[str, object], str, str]] = []
    new_updates = [
        item
        for item in updates
        if isinstance(item, dict)
        and isinstance(item.get("scope_id"), str)
        and locate_scope_page(vault, str(item["scope_id"])) is None
    ]
    proposals = iter(_address_proposals(vault, len(new_updates)))
    address_reservations: list[tuple[str, str]] = []
    transaction_id = payload.get("transaction_id")
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", transaction_id):
        raise MbError("invalid publish transaction_id", 1)
    for raw in updates:
        if not isinstance(raw, dict):
            raise MbError("invalid publish update", 1)
        scope_id = raw.get("scope_id")
        scope_type = raw.get("scope_type")
        managed = raw.get("managed_markdown")
        bank_revision = raw.get("bank_revision")
        if not isinstance(scope_id, str) or scope_type not in {"project", "workspace"} or not isinstance(managed, str):
            raise MbError("invalid publish update", 1)
        if allowed_scope_ids is not None and scope_id not in allowed_scope_ids:
            raise MbError(f"publish update is outside the current scope: {scope_id}", 1)
        _validate_managed(managed)
        if scope_type == "project":
            record = catalog.project(scope_id)
            repository_key = record.repository_keys[0] if record.repository_keys else None
        else:
            record = catalog.workspace(scope_id)
            repository_key = None
        bank = contained_path(vault, record.bank_path, "memory-bank")
        if bank_revision != v2_bank_revision(bank):
            raise MbError(f"Bank revision changed for {scope_id}; regenerate publish", 3)
        path = locate_scope_page(vault, scope_id)
        previous_state = _read_state(vault, scope_id)
        if scope_type == "project" and previous_state and roots_for_scope is not None:
            old_cursors = previous_state.get("git_cursors", [])
            for root in roots_for_scope("project", scope_id):
                current = collect_git_evidence(root)
                candidate_heads: list[str] = []
                if isinstance(old_cursors, list):
                    candidate_heads = [
                        str(item["head"])
                        for item in old_cursors
                        if isinstance(item, dict)
                        and item.get("branch") == current.branch
                        and isinstance(item.get("head"), str)
                    ]
                elif isinstance(old_cursors, dict):
                    legacy_head = old_cursors.get(str(root.resolve()))
                    if isinstance(legacy_head, str):
                        candidate_heads = [legacy_head]
                for old_head in candidate_heads:
                    if current.head:
                        ancestry = subprocess.run(
                            ["git", "-C", str(root), "merge-base", "--is-ancestor", old_head, current.head],
                            capture_output=True,
                            check=False,
                        )
                        if ancestry.returncode != 0:
                            raise MbError(
                                f"published Git cursor is not an ancestor for {scope_id}; establish a new baseline",
                                3,
                            )
        git_cursors = raw.get("git_cursors", [])
        if not isinstance(git_cursors, list) or not all(
            isinstance(item, dict)
            and set(item) == {"branch", "head"}
            and isinstance(item.get("branch"), str)
            and isinstance(item.get("head"), str)
            for item in git_cursors
        ):
            raise MbError("publish git_cursors must contain portable branch/head entries", 1)
        title = str(raw.get("title") or f"{record.display_name} {'Project' if scope_type == 'project' else 'Workspace'} Knowledge")
        if not title.strip() or "\n" in title or "\r" in title:
            raise MbError("publish title must be non-empty single-line text", 1)
        if path:
            current_bytes = (vault / path).read_bytes()
            expected_page = raw.get("page_hash")
            if expected_page != content_hash(current_bytes):
                raise MbError(f"knowledge page changed for {scope_id}; regenerate publish", 3)
            current = current_bytes.decode("utf-8")
            state = _read_state(vault, scope_id)
            _, _, old_block = _managed(current, scope_type)
            if state is not None and state.get("managed_hash") != sha256(old_block.encode("utf-8")).hexdigest():
                raise MbError(f"managed knowledge was edited for {scope_id}; review the conflict", 3)
            page, _ = _replace_page(current, scope_type, scope_id, managed)
        else:
            path = f"wiki/sources/{_slug(record.display_name)}-{'project' if scope_type == 'project' else 'workspace'}-knowledge.md"
            if (vault / path).exists():
                raise MbError(f"knowledge target already exists without matching scope ID: {path}", 3)
            proposed_address = next(proposals, None)
            supplied_address = raw.get("address")
            if proposed_address is not None and supplied_address not in {None, proposed_address}:
                raise MbError(
                    f"new knowledge page requires previewed address {proposed_address}: {scope_id}", 3
                )
            page = _new_page(scope_type, scope_id, title, managed, record.bank_path, repository_key)
            if proposed_address is not None:
                page = patch_scalars(page, {"address": proposed_address})
                address_reservations.append((path, proposed_address))
        block = _managed(page, scope_type)[2]
        managed_hash = sha256(block.encode("utf-8")).hexdigest()
        state = {
            "schema_version": 1,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "page_path": path,
            "managed_hash": managed_hash,
            "bank_revision": bank_revision,
            "content_fingerprint": sha256(managed.strip().encode("utf-8")).hexdigest(),
            "transaction_id": transaction_id,
            "git_cursors": git_cursors,
        }
        state_path = f"memory-bank/publish-state/{scope_id}.json"
        previous_state = _read_state(vault, scope_id)
        if previous_state is not None and previous_state.get("content_fingerprint") == state["content_fingerprint"]:
            continue
        expected_hashes[path] = raw.get("page_hash")
        state_expected = expected.get(state_path)
        if state_expected is not None and not isinstance(state_expected, str):
            raise MbError("invalid publish-state expected hash", 1)
        expected_hashes[state_path] = state_expected
        outputs[path] = page.encode("utf-8")
        outputs[state_path] = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        changed_scopes.append(scope_id)
        changed_entries.append((raw, title, path))
    if not changed_scopes:
        return PublishPlan(transaction_id, {}, expected_hashes, (), ())
    for relative in SHARED:
        current = (vault / relative).read_bytes()
        if expected_hashes.get(relative) != content_hash(current):
            raise MbError(f"shared wiki file changed: {relative}; regenerate publish", 3)
    index = (vault / "wiki/index.md").read_text(encoding="utf-8")
    sources_index = (vault / "wiki/sources/_index.md").read_text(encoding="utf-8")
    for raw, title, page_path in changed_entries:
        summary = str(raw.get("summary") or "Managed project knowledge")
        index = _upsert_index(index, str(raw["scope_id"]), title, summary, page_path, False)
        sources_index = _upsert_index(
            sources_index, str(raw["scope_id"]), title, summary, page_path, True
        )
    outputs["wiki/index.md"] = index.encode("utf-8")
    outputs["wiki/sources/_index.md"] = sources_index.encode("utf-8")
    log = (vault / "wiki/log.md").read_text(encoding="utf-8")
    page_paths = tuple(path for path in outputs if path.startswith("wiki/sources/") and path != "wiki/sources/_index.md")
    outputs["wiki/log.md"] = _prepend_log(
        log, ", ".join(title for _, title, _ in changed_entries), page_paths, transaction_id
    ).encode("utf-8")
    split_frontmatter(hot_content)
    outputs["wiki/hot.md"] = patch_scalars(hot_content, {"updated": date.today().isoformat()}).encode("utf-8")
    manifest_path = vault / ".raw/.manifest.json"
    if address_reservations and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError("invalid .raw/.manifest.json", 4) from exc
        if not isinstance(manifest, dict):
            raise MbError("invalid .raw/.manifest.json", 4)
        address_map = manifest.setdefault("address_map", {})
        if not isinstance(address_map, dict):
            raise MbError("invalid .raw/.manifest.json address_map", 4)
        for page_path, address in address_reservations:
            existing = address_map.get(page_path)
            if existing not in {None, address}:
                raise MbError(f"address_map conflicts for {page_path}", 3)
            address_map[page_path] = address
        outputs[".raw/.manifest.json"] = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    return PublishPlan(
        transaction_id,
        outputs,
        expected_hashes,
        tuple(changed_scopes),
        tuple(address_reservations),
    )


@contextmanager
def _wiki_lock(vault: Path, relative: str) -> Iterator[None]:
    script = vault / "scripts/wiki-lock.sh"
    acquired = subprocess.run(["bash", str(script), "acquire", relative], cwd=vault, capture_output=True, text=True)
    if acquired.returncode != 0:
        raise MbError(f"unable to acquire wiki lock for {relative}", 5)
    try:
        yield
    finally:
        subprocess.run(["bash", str(script), "release", relative], cwd=vault, capture_output=True)


def apply_publish_plan(plan: PublishPlan, vault: Path, state_root: Path) -> dict[str, object]:
    if not plan.outputs:
        return {"transaction_id": plan.transaction_id, "changed_scopes": [], "files": [], "noop": True}
    transport = VaultTransport.load(vault)
    with global_lock(state_root), ExitStack() as stack:
        wiki_paths = sorted(
            path for path in plan.outputs if path.startswith("wiki/") or path == ".raw/.manifest.json"
        )
        for relative in wiki_paths:
            stack.enter_context(_wiki_lock(vault, relative))
        for relative in plan.outputs:
            observed = content_hash(transport.read(relative))
            if observed != plan.expected_hashes.get(relative):
                raise MbError(f"publish target changed: {relative}; regenerate preview", 3)
        allocator = vault / "scripts/allocate-address.sh"
        for _, expected_address in plan.address_reservations:
            reserved = subprocess.run(
                ["bash", str(allocator)], cwd=vault, capture_output=True, text=True, check=False
            )
            if reserved.returncode != 0 or reserved.stdout.strip() != expected_address:
                raise MbError(
                    "knowledge address changed after preview; regenerate publish before writing",
                    3,
                )
        transaction = BackupTransaction.create(Path(state_root) / "transactions", plan.transaction_id)
        originals = {}
        for relative in plan.outputs:
            path = contained_path(vault, relative)
            originals[relative] = transport.read(relative)
            if path.exists():
                transaction.capture(path, "publish")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                transaction.capture_missing(path, "publish")
        transaction.finish()
        applied: list[str] = []
        try:
            for relative, content in plan.outputs.items():
                applied.append(relative)
                transport.write(relative, content)
        except Exception as write_error:
            rollback_errors: list[str] = []
            for relative in reversed(applied):
                original = originals[relative]
                try:
                    if original is None:
                        transport.remove(relative)
                    else:
                        transport.write(relative, original)
                except Exception as rollback_error:
                    rollback_errors.append(f"{relative}: {rollback_error}")
            if rollback_errors:
                raise MbError(
                    "publish failed and rollback durability could not be confirmed: "
                    + "; ".join(rollback_errors),
                    4,
                ) from write_error
            transaction.mark_complete(
                "rolled_back",
                {"transaction_id": plan.transaction_id, "error": str(write_error)},
            )
            raise
        transaction.mark_complete(
            "committed",
            {"transaction_id": plan.transaction_id, "files": list(plan.outputs)},
        )
        return {
            "transaction_id": plan.transaction_id,
            "changed_scopes": list(plan.changed_scopes),
            "files": list(plan.outputs),
            "noop": False,
        }
