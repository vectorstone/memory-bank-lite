"""Handoff document: a pointer-only resume note for the next agent.

A handoff deliberately *references* Bank entries, notes and repository paths
instead of restating their content, so it can never become a second, drifting
copy of the Bank. The only free text it carries is the next session's focus and
whatever genuinely exists nowhere else.

``handoff.md`` owns a managed block, exactly like ``lessons.md`` does for
distilled lessons. A superseded handoff moves verbatim into
``archive/handoff-<year>.md``, so the newest document is always the whole of
``handoff.md`` while older ones stay recallable.
"""

from dataclasses import dataclass
import re

from .errors import MbError

HANDOFF_FILE = "handoff.md"
HANDOFF_START = "<!-- mb-lite:handoff:start -->"
HANDOFF_END = "<!-- mb-lite:handoff:end -->"
HANDOFF_HEADING = "## Latest Handoff"

MARKER_RE = re.compile(r"<!-- mb-lite-handoff:([0-9]{8}T[0-9]{6}Z)-[0-9a-f]{8} -->")
BLOCK_RE = re.compile(
    re.escape(HANDOFF_START) + r"(.*?)" + re.escape(HANDOFF_END),
    re.DOTALL,
)
H1_RE = re.compile(r"(?m)^#\s+.+$")

FOCUS_MAX_CHARS = 800
NOTE_MAX_CHARS = 4000
SUMMARY_MAX_BYTES = 2048


@dataclass(frozen=True)
class HandoffContext:
    """Everything a handoff may say, resolved from the Bank by the caller."""

    timestamp: str
    marker: str
    summary: str
    focus: str = ""
    note: str = ""
    key_files: tuple[str, ...] = ()
    note_relative: str | None = None
    open_items: int = 0
    attention_ids: tuple[str, ...] = ()
    lessons: bool = False


def render_handoff_body(context: HandoffContext) -> str:
    heading_time = context.timestamp.replace("T", " ").removesuffix("Z")
    lines = [
        HANDOFF_HEADING,
        "",
        f"<!-- mb-lite-handoff:{context.marker} -->",
        f"### {heading_time} - {context.summary}",
        "",
        f"**Focus for next session:** {context.focus or 'Not specified'}",
    ]
    if context.key_files:
        lines.extend(("", "**Key files:** " + ", ".join(f"`{value}`" for value in context.key_files)))
    references = ["`status.md`", "`checklist.md`"]
    if context.open_items:
        references[-1] += f" ({context.open_items} open)"
    references.append(
        "`attention.md`"
        + (
            " (" + ", ".join(f"`{item}`" for item in context.attention_ids) + ")"
            if context.attention_ids
            else ""
        )
    )
    if context.lessons:
        references.append("`lessons.md`")
    lines.extend(("", "**Detail lives in the Bank** (referenced, not copied):"))
    lines.append(f"- `progress.md` entry `<!-- mb-lite-entry:{context.marker} -->`")
    lines.append("- " + ", ".join(references))
    if context.note_relative:
        lines.append(f"- note `{context.note_relative}`")
    if context.note:
        lines.extend(("", "**Only in this handoff**", context.note))
    lines.extend(("", "> Read the referenced files for detail; this handoff does not copy them."))
    return "\n".join(lines).rstrip() + "\n"


def handoff_body(source: str) -> str | None:
    """Return the managed block's content, or ``None`` when there is no block."""
    match = BLOCK_RE.search(source)
    return match.group(1).strip() if match else None


def handoff_marker(source: str) -> str | None:
    match = MARKER_RE.search(source)
    return match.group(1) if match else None


def compose_handoff_file(existing: str | None, body: str) -> str:
    """Replace handoff.md's managed block, creating the file lazily.

    Mirrors how ``distill`` maintains ``lessons.md``: an existing block is
    replaced in place, surrounding user text is preserved, and a file without a
    block gains one after its H1.
    """
    block = f"{HANDOFF_START}\n{body.rstrip()}\n{HANDOFF_END}\n"
    if existing is None:
        return f"# Handoff\n\n{block}"
    starts = existing.count(HANDOFF_START)
    ends = existing.count(HANDOFF_END)
    if starts or ends:
        if starts != 1 or ends != 1:
            raise MbError("handoff.md must contain exactly one managed block", 4)
        match = BLOCK_RE.search(existing)
        assert match is not None
        content = existing[: match.start()] + block + existing[match.end():]
    else:
        heading = H1_RE.search(existing)
        if heading is None:
            content = f"# Handoff\n\n{block}{existing}"
        else:
            content = existing[: heading.end()] + "\n\n" + block + existing[heading.end():]
    return content if content.endswith("\n") else content + "\n"


def plan_handoff_archive(existing: str | None, fallback_year: str) -> tuple[str, str] | None:
    """Return ``(archive path, text to append)`` for a superseded handoff.

    The superseded version moves verbatim - never summarized - so the archive
    keeps the full history. A hand-written ``handoff.md`` without a managed
    block is archived whole rather than dropped.
    """
    if existing is None:
        return None
    body = handoff_body(existing)
    if body is None:
        body = existing.strip()
    if not body:
        return None
    marker = handoff_marker(existing)
    year = marker[:4] if marker else fallback_year
    return f"archive/handoff-{year}.md", body.rstrip() + "\n"


def archive_header(relative: str) -> str:
    year = relative[len("archive/handoff-"):-len(".md")]
    return f"# Handoff Archive {year}"
