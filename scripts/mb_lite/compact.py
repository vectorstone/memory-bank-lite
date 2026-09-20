"""Explicit Bank compaction: progress rolling and attention archiving.

Compaction moves content verbatim; it never summarizes or deletes. Closed
attention items and progress entries beyond the rolling limits are relocated
into ``archive/`` where recall still finds them.
"""

from pathlib import Path

from .archive import (
    PROGRESS_MAX_BYTES,
    PROGRESS_MAX_ENTRIES,
    merge_archive_text,
    plan_progress_roll,
    progress_archive_name,
    split_progress,
)
from .attention import (
    parse_attention,
    parse_attention_entries,
    render_attention,
    render_attention_archive,
)
from .bank import validate_v2_bank
from .bank_fs import read_bank_text
from .done import _bank_lock, _write_all_or_rollback
from .errors import MbError


def _progress_state(bank: Path) -> dict[str, object]:
    source = read_bank_text(bank, "progress.md", required=True) or ""
    _, entries = split_progress(source)
    roll = plan_progress_roll(source)
    return {
        "entries": len(entries),
        "bytes": len(source.encode("utf-8")),
        "retained_entries": len(entries) - roll.archived_count,
        "retained_bytes": len(roll.retained.encode("utf-8")),
        "would_archive": roll.archived_count,
        "limits": {"max_entries": PROGRESS_MAX_ENTRIES, "max_bytes": PROGRESS_MAX_BYTES},
    }


def _attention_state(bank: Path) -> dict[str, object]:
    active_source = read_bank_text(bank, "attention.md", required=True) or ""
    archive_source = read_bank_text(bank, "archive/attention.md")
    entries = parse_attention(active_source)
    archived = parse_attention_entries(archive_source) if archive_source else ()
    closed = tuple(entry for entry in entries if entry.status != "active")
    return {
        "active": len(entries) - len(closed),
        "would_archive": len(closed),
        "already_archived": len(archived),
    }


def compact_preview(bank: Path) -> dict[str, object]:
    bank = validate_v2_bank(Path(bank))
    progress = _progress_state(bank)
    attention = _attention_state(bank)
    pending = bool(progress["would_archive"] or attention["would_archive"])
    return {
        "bank_path": str(bank),
        "progress": progress,
        "attention": attention,
        "changes_pending": pending,
    }


def apply_compact(bank: Path) -> dict[str, object]:
    bank = validate_v2_bank(Path(bank))
    with _bank_lock(bank):
        progress_source = read_bank_text(bank, "progress.md", required=True) or ""
        roll = plan_progress_roll(progress_source)
        active_source = read_bank_text(bank, "attention.md", required=True) or ""
        archive_source = read_bank_text(bank, "archive/attention.md")
        entries = parse_attention(active_source)
        archived = parse_attention_entries(archive_source) if archive_source else ()
        closed = tuple(entry for entry in entries if entry.status != "active")
        active = tuple(entry for entry in entries if entry.status == "active")

        outputs: list[tuple[Path, str]] = []
        if roll.archived:
            outputs.append((bank / "progress.md", roll.retained))
            by_file: dict[str, list[str]] = {}
            for entry in roll.archived:
                by_file.setdefault(progress_archive_name(entry), []).append(entry.raw)
            for relative, additions in sorted(by_file.items()):
                existing = read_bank_text(bank, relative)
                year = relative[len("archive/progress-"):-len(".md")]
                outputs.append(
                    (
                        bank / relative,
                        merge_archive_text(
                            existing, tuple(additions), f"# Progress Archive {year}",
                        ),
                    )
                )
        if closed:
            outputs.append((bank / "attention.md", render_attention(active_source, active)))
            merged_closed = tuple(archived) + closed
            outputs.append(
                (bank / "archive/attention.md", render_attention_archive(merged_closed) or ""),
            )
        if not outputs:
            raise MbError("nothing to compact; Bank is already within limits", 1)
        for path, _ in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
        _write_all_or_rollback(tuple(outputs))
        return {
            "bank_path": str(bank),
            "archived_entries": roll.archived_count,
            "archived_attention": len(closed),
            "progress_bytes_before": len(progress_source.encode("utf-8")),
            "progress_bytes_after": len(roll.retained.encode("utf-8")),
        }
