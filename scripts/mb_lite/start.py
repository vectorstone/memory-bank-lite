from pathlib import Path
import re
from datetime import date

from .attention import parse_attention
from .bank import validate_bank, validate_v2_bank
from .bank_fs import bank_markdown_files, read_bank_text
from .errors import MbError
from .handoff import HANDOFF_FILE, HANDOFF_HEADING, handoff_body

MAX_BYTES = 8192
HANDOFF_MAX_BYTES = 2048
LESSON_SUMMARY_MAX = 3
LESSON_TITLE_RE = re.compile(r"(?m)^###\s+(LSN-[0-9]{3,})\s+(.+?)\s*$")
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


def _lesson_lines(bank: Path) -> list[str]:
    source = read_bank_text(bank, "lessons.md")
    if source is None:
        return []
    return [
        f"- {lesson_id} {title}"
        for lesson_id, title in LESSON_TITLE_RE.findall(source)
    ]


def _lesson_summary(lines: list[str], limit: int = LESSON_SUMMARY_MAX) -> list[str]:
    """Bounded stable conclusions, always with an omission count when truncated."""
    if limit <= 0:
        return []
    shown = lines[:limit]
    omitted = len(lines) - len(shown)
    if omitted:
        shown = [*shown, f"- ... {omitted} more lessons; use start --section lessons"]
    return shown


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


def _render(current, open_items, stage_title, stage_detail, lessons, recent, note_titles, trimmed):
    sections = ["# Memory Bank", "## Current", current, "## Open Items", open_items]
    if stage_title:
        stage = f"### {stage_title}"
        if stage_detail:
            stage += "\n" + stage_detail
        sections.extend(("## Active Stage", stage))
    if lessons:
        sections.extend(("## Verified Lessons", "\n".join(lessons)))
    if recent:
        sections.extend(("## Recent Progress", "\n\n".join(recent)))
    if note_titles:
        sections.extend(("## Matching Notes", "\n".join(f"- {title}" for title in note_titles)))
    if trimmed:
        sections.extend(("## Notice", "Older optional context was trimmed to stay within 8192 bytes."))
    return "\n\n".join(sections).rstrip() + "\n"


def build_start_context(bank: Path, section: str | None = None, *, max_bytes: int = MAX_BYTES) -> str:
    bank = Path(bank)
    validate_bank(bank)
    current = _current(read_bank_text(bank, "status.md", required=True) or "")
    open_items = _open_items(read_bank_text(bank, "checklist.md", required=True) or "")
    recent = _recent(read_bank_text(bank, "progress.md", required=True) or "")
    stage_title, stage_detail = _active_stage(bank)
    all_lessons = _lesson_lines(bank)
    lesson_limit = LESSON_SUMMARY_MAX
    lessons = _lesson_summary(all_lessons, lesson_limit)
    note_titles = _note_titles(bank, _focus(current), stage_title)
    values = {
        "current": current,
        "open-items": open_items,
        "active-stage": f"### {stage_title}\n{stage_detail}".rstrip() if stage_title else "No active stage.",
        "lessons": "\n".join(all_lessons) or "No distilled lessons.",
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
    protected = _render(current, open_items, "", "", [], [], [], False)
    if len(protected.encode("utf-8")) > MAX_BYTES:
        raise MbError(f"protected start sections exceed 8192 bytes (current={len(current.encode('utf-8'))}, open-items={len(open_items.encode('utf-8'))}); use start --section or recall", 1)
    trimmed = False
    while True:
        candidate = _render(current, open_items, stage_title, stage_detail, lessons, recent, note_titles, trimmed)
        size = len(candidate.encode("utf-8"))
        if size <= max_bytes:
            return candidate
        trimmed = True
        if note_titles:
            note_titles.pop()
        elif recent:
            recent.pop(0)
        elif stage_detail:
            stage_detail = ""
        elif lesson_limit:
            lesson_limit -= 1
            lessons = _lesson_summary(all_lessons, lesson_limit)
        elif size > MAX_BYTES:
            raise MbError("start output exceeds 8192 bytes after optional trimming", 1)
        else:
            # Optional content is exhausted but the render still fits the hard cap:
            # return it as-is so the caller can degrade the assembled tail.
            return candidate


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


def _handoff_context(bank: Path) -> list[str]:
    """Read the handoff block, minus the heading the start section already owns."""
    source = read_bank_text(bank, HANDOFF_FILE)
    if source is None:
        return []
    body = handoff_body(source)
    if body is None:
        return []
    lines = body.splitlines()
    if lines and lines[0].strip() == HANDOFF_HEADING:
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return [line.rstrip() for line in lines]


def _insert_handoff(output: str, lines: list[str], max_bytes: int = MAX_BYTES) -> str:
    """Insert the handoff section, dropping trailing lines under size pressure.

    The handoff outranks the attention and member summaries that are appended
    after it, because it is the first thing a fresh agent should read. Whole
    lines are dropped and counted, never truncated mid-line.
    """
    if not lines:
        return output
    marker = "\n\n## Attention\n\n"
    if marker not in output:
        marker = "\n\n## Open Items\n\n"
    visible = list(lines)
    while visible:
        omitted = len(lines) - len(visible)
        body_lines = list(visible)
        if omitted:
            body_lines.append(f"- ... {omitted} handoff lines omitted; use start --section handoff")
        body = "\n".join(body_lines)
        candidate = output.replace(marker, f"\n\n## Handoff\n\n{body}{marker}", 1)
        if len(body.encode("utf-8")) <= HANDOFF_MAX_BYTES and len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
        visible.pop()
    dropped = f"- ... {len(lines)} handoff lines omitted; use start --section handoff"
    candidate = output.replace(marker, f"\n\n## Handoff\n\n{dropped}{marker}", 1)
    if len(candidate.encode("utf-8")) <= max_bytes:
        return candidate
    return output


def _omitted_attention_notice(count: int) -> str:
    return f"- ... {count} attention items omitted; use start --section attention"


def _insert_attention(output: str, attention: list[str], max_bytes: int = MAX_BYTES) -> str:
    """Insert the attention block before Open Items, degrading under pressure.

    Entries are dropped lowest-severity first (the list arrives sorted high to
    low) and every drop is counted in an explicit notice, so nothing disappears
    silently. max_bytes may sit below MAX_BYTES when the caller reserved room
    for its own tail blocks; if even the omission notice cannot fit that
    reduced budget, it still degrades within the hard cap rather than failing
    closed, so protected sections keep priority. Only a Bank whose protected
    sections leave no room for even the notice at the hard cap fails closed,
    mirroring the protected-section contract.
    """
    marker = "\n\n## Open Items\n\n"
    if marker not in output:
        return output
    for shown in range(len(attention), 0, -1):
        lines = list(attention[:shown])
        if shown < len(attention):
            lines.append(_omitted_attention_notice(len(attention) - shown))
        block = "\n\n## Attention\n\n" + "\n".join(lines)
        candidate = output.replace(marker, block + marker, 1)
        if len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
    notice_block = "\n\n## Attention\n\n" + _omitted_attention_notice(len(attention))
    candidate = output.replace(marker, notice_block + marker, 1)
    if len(candidate.encode("utf-8")) <= max_bytes:
        return candidate
    if len(candidate.encode("utf-8")) <= MAX_BYTES:
        # The caller's extra reserve over-promised; keep the notice within the
        # hard cap so protected sections and this block beat the reserve.
        return candidate
    raise MbError("protected start sections exceed 8192 bytes after attention", 1)


def _handoff_reserve(lines: list[str]) -> int:
    """Bytes the capped handoff section will claim in the assembled output."""
    if not lines:
        return 0
    body = min(len("\n".join(lines).encode("utf-8")), HANDOFF_MAX_BYTES)
    return len("\n\n## Handoff\n\n".encode("utf-8")) + body


def _attention_reserve(lines: list[str]) -> int:
    """Bytes guaranteed to the mandatory attention block: its heading plus room
    for the shortest entry line and the omission notice."""
    marker = len("\n\n## Attention\n\n".encode("utf-8"))
    if not lines:
        return marker + len("No active attention.".encode("utf-8"))
    smallest = min(len(line.encode("utf-8")) for line in lines)
    notice = len(_omitted_attention_notice(len(lines)).encode("utf-8"))
    return marker + smallest + notice


def _omitted_members_notice(count: int) -> str:
    return f"- ... {count} member summaries omitted"


def _members_reserve(members: list[str]) -> int:
    """Bytes guaranteed to the Members block: its heading plus room for the
    shortest member line and the omission notice (or the empty placeholder)."""
    marker = len("\n\n## Members\n\n".encode("utf-8"))
    if not members:
        return marker + len("No member summaries available.".encode("utf-8"))
    smallest = min(len(line.encode("utf-8")) for line in members)
    notice = len(_omitted_members_notice(len(members)).encode("utf-8"))
    return marker + smallest + notice


def build_v2_start_context(
    bank: Path,
    section: str | None = None,
    *,
    extra_reserve: int = 0,
) -> str:
    validate_v2_bank(bank)
    attention = _attention_context(bank)
    handoff = _handoff_context(bank)
    if section == "attention":
        output = "## attention\n\n" + ("\n".join(attention) if attention else "No active attention.") + "\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError("start section attention exceeds 8192 bytes; use recall", 1)
        return output
    if section == "handoff":
        output = "## handoff\n\n" + ("\n".join(handoff) if handoff else "No handoff recorded.") + "\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError("start section handoff exceeds 8192 bytes; use recall", 1)
        return output
    if section is None:
        # Trim optional content against the cap minus the mandatory tail, so the
        # v1 trim loop cannot pack recent progress up to the full cap and starve
        # the handoff/attention block (2026-11-08 incident). extra_reserve carves
        # out room for tail blocks appended by the caller, such as the workspace
        # Members section (2026-11-22 incident).
        budget = (
            MAX_BYTES
            - _handoff_reserve(handoff)
            - _attention_reserve(attention)
            - extra_reserve
        )
        base = build_start_context(bank, None, max_bytes=budget)
    else:
        base = build_start_context(bank, section)
    if section is not None:
        return base
    if not attention:
        attention = ["No active attention."]
    cap = MAX_BYTES - extra_reserve
    output = _insert_handoff(base, handoff, cap)
    return _insert_attention(output, attention, cap)


def build_workspace_start_context(
    bank: Path,
    member_contexts: tuple[tuple[str, str], ...],
    section: str | None = None,
) -> str:
    """Assemble the Workspace start context.

    The Members block is a mandatory tail, so its budget is reserved up front
    (heading plus the shortest member line and the omission notice) instead of
    letting the v1 trim loop pack the workspace Bank up to the cap. Under size
    pressure members degrade to a counted notice — down to zero visible
    members — before failing closed.
    """
    lines = [f"- {name}: {context}" for name, context in member_contexts]
    members = lines or ["No member summaries available."]
    if section == "members":
        output = "## members\n\n" + "\n".join(members) + "\n"
        if len(output.encode("utf-8")) > MAX_BYTES:
            raise MbError("start section members exceeds 8192 bytes; use recall", 1)
        return output
    if section is not None:
        return build_v2_start_context(bank, section)
    base = build_v2_start_context(bank, None, extra_reserve=_members_reserve(members))
    marker = "\n\n## Open Items\n\n"
    for shown in range(len(members), -1, -1):
        body_lines = members[:shown]
        if shown < len(members):
            body_lines.append(_omitted_members_notice(len(members) - shown))
        candidate = base.replace(
            marker, "\n\n## Members\n\n" + "\n".join(body_lines) + marker, 1
        )
        if len(candidate.encode("utf-8")) <= MAX_BYTES:
            return candidate
    raise MbError("Workspace start output exceeds 8192 bytes after member trimming", 1)
