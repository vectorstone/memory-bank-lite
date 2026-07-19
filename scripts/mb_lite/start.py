from pathlib import Path
import re
from datetime import date

from .attention import parse_attention
from .bank import validate_bank, validate_v2_bank
from .bank_fs import bank_markdown_files, read_bank_text
from .errors import MbError

MAX_BYTES = 8192
CURRENT_RE = re.compile(r"<!-- mb-lite:current:start -->(.*?)<!-- mb-lite:current:end -->", re.DOTALL)
ENTRY_RE = re.compile(r"(?=<!-- mb-lite-entry:)(<!-- mb-lite-entry:[^>]+ -->.*?)(?=<!-- mb-lite-entry:|\Z)", re.DOTALL)
STAGE_RE = re.compile(r"(?ms)^###\s+(Stage\s+\d+:\s*.+?)\s*$\n(.*?)(?=^###\s+Stage\s+\d+:|\Z)")


def _current(status: str) -> str:
    match = CURRENT_RE.search(status)
    if match:
        return match.group(1).strip()
    lines = [line for line in status.splitlines() if line.startswith(("**Focus:**", "**Next:**", "**Blockers:**"))]
    return "\n".join(lines) or "Current state is not recorded."


def _open_items(checklist: str) -> str:
    items = [line for line in checklist.splitlines() if re.match(r"^\s*[-*]\s+\[\s\]\s+", line)]
    return "\n".join(items) or "No open items."


def _focus(current: str) -> str:
    match = re.search(r"(?m)^\*\*Focus:\*\*\s*(.+)$", current)
    return match.group(1).strip() if match else ""


def _active_stage(bank: Path) -> tuple[str, str]:
    for _, source in reversed(bank_markdown_files(bank, "plans")):
        for match in STAGE_RE.finditer(source):
            if re.search(r"(?m)^\s*[-*]\s+\[\s\]\s+", match.group(2)):
                return match.group(1).strip(), match.group(2).strip()
    return "", ""


def _terms(value: str) -> tuple[str, ...]:
    return tuple(term for term in re.split(r"[\W_]+", value.casefold()) if len(term) >= 2)


def _note_titles(bank: Path, focus: str, stage_title: str) -> list[str]:
    terms = _terms(f"{focus} {stage_title}")
    if not terms:
        return []
    result = []
    for name, source in bank_markdown_files(bank, "notes"):
        heading = re.search(r"(?m)^#\s+(.+)$", source)
        title = heading.group(1).strip() if heading else Path(name).stem
        if any(term in title.casefold() for term in terms):
            result.append(title)
        if len(result) == 5:
            break
    return result


def _recent(progress: str) -> list[str]:
    lite = [match.group(1).strip() for match in ENTRY_RE.finditer(progress)]
    if len(lite) >= 3:
        return lite[-3:]
    legacy_source = ENTRY_RE.sub("", progress)
    canonical_lite = {
        re.sub(r"^<!-- mb-lite-entry:[^>]+ -->\s*", "", entry).strip()
        for entry in lite
    }
    legacy = []
    for entry in re.findall(r"(?ms)^###\s+.+?(?=^###\s+|\Z)", legacy_source):
        normalized = entry.strip()
        if normalized not in canonical_lite:
            legacy.append(normalized)
    return legacy[-(3 - len(lite)):] + lite


def _render(current, open_items, stage_title, stage_detail, recent, note_titles, trimmed):
    sections = ["# Memory Bank", "## Current", current, "## Open Items", open_items]
    if stage_title:
        stage = f"### {stage_title}"
        if stage_detail:
            stage += "\n" + stage_detail
        sections.extend(("## Active Stage", stage))
    if recent:
        sections.extend(("## Recent Progress", "\n\n".join(recent)))
    if note_titles:
        sections.extend(("## Matching Notes", "\n".join(f"- {title}" for title in note_titles)))
    if trimmed:
        sections.extend(("## Notice", "Older optional context was trimmed to stay within 8192 bytes."))
    return "\n\n".join(sections).rstrip() + "\n"


def build_start_context(bank: Path, section: str | None = None) -> str:
    bank = Path(bank)
    validate_bank(bank)
    current = _current(read_bank_text(bank, "status.md", required=True) or "")
    open_items = _open_items(read_bank_text(bank, "checklist.md", required=True) or "")
    recent = _recent(read_bank_text(bank, "progress.md", required=True) or "")
    stage_title, stage_detail = _active_stage(bank)
    note_titles = _note_titles(bank, _focus(current), stage_title)
    values = {
        "current": current,
        "open-items": open_items,
        "active-stage": f"### {stage_title}\n{stage_detail}".rstrip() if stage_title else "No active stage.",
        "recent-progress": "\n\n".join(recent) or "No recent progress.",
        "notes": "\n".join(f"- {title}" for title in note_titles) or "No matching notes.",
    }
    if section is not None:
        if section not in values:
            raise MbError(f"unknown start section: {section}", 1)
        output = f"## {section}\n\n{values[section].rstrip()}\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError(f"start section {section} exceeds 8192 bytes; use recall", 1)
        return output
    protected = _render(current, open_items, "", "", [], [], False)
    if len(protected.encode("utf-8")) > MAX_BYTES:
        raise MbError(f"protected start sections exceed 8192 bytes (current={len(current.encode('utf-8'))}, open-items={len(open_items.encode('utf-8'))}); use start --section or recall", 1)
    trimmed = False
    while len(_render(current, open_items, stage_title, stage_detail, recent, note_titles, trimmed).encode("utf-8")) > MAX_BYTES:
        trimmed = True
        if note_titles:
            note_titles.pop()
        elif recent:
            recent.pop(0)
        elif stage_detail:
            stage_detail = ""
        else:
            raise MbError("start output exceeds 8192 bytes after optional trimming", 1)
    return _render(current, open_items, stage_title, stage_detail, recent, note_titles, trimmed)


def _attention_context(bank: Path) -> list[str]:
    source = read_bank_text(bank, "attention.md", required=True) or ""
    severity_order = {"high": 0, "medium": 1, "low": 2}
    active = [entry for entry in parse_attention(source) if entry.status == "active"]
    active.sort(key=lambda entry: (severity_order[entry.severity], entry.attention_id))
    today = date.today()
    return [
        f"- [{entry.severity}] {entry.attention_id} {entry.title}"
        + (" (review due)" if entry.review_due(today) else "")
        for entry in active
    ]


def build_v2_start_context(bank: Path, section: str | None = None) -> str:
    validate_v2_bank(bank)
    attention = _attention_context(bank)
    if section == "attention":
        output = "## attention\n\n" + ("\n".join(attention) if attention else "No active attention.") + "\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError("start section attention exceeds 8192 bytes; use recall", 1)
        return output
    base = build_start_context(bank, section)
    if section is not None:
        return base
    if not attention:
        attention = ["No active attention."]
    marker = "\n\n## Open Items\n\n"
    while True:
        block = "\n\n## Attention\n\n" + "\n".join(attention)
        output = base.replace(marker, block + marker, 1)
        if len(output.encode("utf-8")) <= MAX_BYTES:
            return output
        if len(attention) <= 1:
            raise MbError("protected start sections exceed 8192 bytes after attention", 1)
        attention.pop()


def build_workspace_start_context(
    bank: Path,
    member_contexts: tuple[tuple[str, str], ...],
    section: str | None = None,
) -> str:
    lines = [f"- {name}: {context}" for name, context in member_contexts]
    members = lines or ["No member summaries available."]
    if section == "members":
        output = "## members\n\n" + "\n".join(members) + "\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError("start section members exceeds 8192 bytes; use recall", 1)
        return output
    base = build_v2_start_context(bank, section)
    if section is not None:
        return base
    marker = "\n\n## Open Items\n\n"
    omitted = 0
    while True:
        visible = members[: max(1, len(members) - omitted)]
        body = "\n".join(visible)
        if omitted:
            body += f"\n- ... {omitted} member summaries omitted"
        output = base.replace(marker, f"\n\n## Members\n\n{body}" + marker, 1)
        if len(output.encode("utf-8")) <= MAX_BYTES:
            return output
        if len(visible) <= 1:
            raise MbError("Workspace start output exceeds 8192 bytes", 1)
        omitted += 1
