from dataclasses import dataclass
from datetime import date, timedelta
import re

from .errors import MbError


START = "<!-- mb-lite:attention:start -->"
END = "<!-- mb-lite:attention:end -->"
ENTRY_RE = re.compile(
    r"<!-- mb-lite-attention:(ATT-[0-9]{3,}) -->\n"
    r"### \1 \[(high|medium|low)\] (.+?)\n\n"
    r"- \*\*Status:\*\* (active|resolved|superseded)\n"
    r"- \*\*Scope:\*\* (.+?)\n"
    r"- \*\*Reason:\*\* (.+?)\n"
    r"- \*\*Evidence:\*\* (.+?)\n"
    r"- \*\*Verified:\*\* ([0-9]{4}-[0-9]{2}-[0-9]{2})\n"
    r"- \*\*Review after:\*\* ([0-9]+) days(?:\n|\Z)",
)


@dataclass(frozen=True)
class AttentionEntry:
    attention_id: str
    title: str
    scope: str
    severity: str
    status: str
    reason: str
    evidence: str
    verified_at: date
    review_after_days: int

    def review_due(self, today: date) -> bool:
        return today > self.verified_at + timedelta(days=self.review_after_days)


def _managed(source: str) -> tuple[int, int, str]:
    if source.count(START) != 1 or source.count(END) != 1:
        raise MbError("attention.md must contain exactly one managed block", 4)
    start = source.index(START)
    end = source.index(END, start) + len(END)
    if source.index(END, start) < start:
        raise MbError("invalid attention.md managed block", 4)
    return start, end, source[start + len(START):end - len(END)]


def _entries_from_text(source: str, *, seen: set[str] | None = None) -> list[AttentionEntry]:
    seen = seen if seen is not None else set()
    entries: list[AttentionEntry] = []
    for match in ENTRY_RE.finditer(source):
        attention_id = match.group(1)
        if attention_id in seen:
            raise MbError(f"duplicate attention ID: {attention_id}", 4)
        seen.add(attention_id)
        try:
            verified = date.fromisoformat(match.group(8))
            review_days = int(match.group(9))
        except ValueError as exc:
            raise MbError(f"invalid attention entry: {attention_id}", 4) from exc
        if review_days < 1 or review_days > 3650:
            raise MbError(f"invalid attention review period: {attention_id}", 4)
        entries.append(
            AttentionEntry(
                attention_id,
                match.group(3).strip(),
                match.group(5).strip(),
                match.group(2),
                match.group(4),
                match.group(6).strip(),
                match.group(7).strip(),
                verified,
                review_days,
            )
        )
    return entries


def parse_attention_entries(source: str) -> tuple[AttentionEntry, ...]:
    """Lenient entry extraction; accepts archive files without managed blocks."""
    return tuple(_entries_from_text(source))


def parse_attention(source: str) -> tuple[AttentionEntry, ...]:
    _, _, body = _managed(source)
    entries = _entries_from_text(body)
    residue = ENTRY_RE.sub("", body)
    residue = re.sub(r"^\s*## Active Attention\s*", "", residue)
    if residue.strip():
        raise MbError("unrecognized content inside managed attention block", 4)
    return tuple(entries)


def render_entry(entry: AttentionEntry) -> str:
    values = (entry.title, entry.scope, entry.reason, entry.evidence)
    if any("\n" in value or not value.strip() for value in values):
        raise MbError(f"attention fields must be single-line text: {entry.attention_id}", 1)
    return (
        f"<!-- mb-lite-attention:{entry.attention_id} -->\n"
        f"### {entry.attention_id} [{entry.severity}] {entry.title}\n\n"
        f"- **Status:** {entry.status}\n"
        f"- **Scope:** {entry.scope}\n"
        f"- **Reason:** {entry.reason}\n"
        f"- **Evidence:** {entry.evidence}\n"
        f"- **Verified:** {entry.verified_at.isoformat()}\n"
        f"- **Review after:** {entry.review_after_days} days\n"
    )


def render_attention(source: str, entries: tuple[AttentionEntry, ...]) -> str:
    start, end, _ = _managed(source)
    rendered = "\n\n".join(render_entry(entry).rstrip() for entry in entries)
    block = f"{START}\n## Active Attention\n"
    if rendered:
        block += f"\n{rendered}\n"
    block += f"\n{END}"
    return source[:start] + block + source[end:]


def next_attention_id(entries: tuple[AttentionEntry, ...]) -> str:
    highest = max((int(entry.attention_id.split("-")[1]) for entry in entries), default=0)
    return f"ATT-{highest + 1:03d}"


ARCHIVE_HEADING = "# Attention Archive"


def render_attention_archive(entries: tuple[AttentionEntry, ...]) -> str | None:
    """Render closed items into the machine-managed archive file."""
    if not entries:
        return None
    body = "\n\n".join(render_entry(entry).rstrip() for entry in entries)
    return f"{ARCHIVE_HEADING}\n\n{body}\n"
