from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .bank import CORE_FILES
from .errors import MbError


def bank_revision(bank: Path) -> str:
    digest = sha256()
    for name in CORE_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((bank / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _string_list(data: dict[str, Any], field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list):
        raise MbError(f"{field} must contain non-empty strings", 1)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MbError(f"{field} must contain non-empty strings", 1)
        stripped = item.strip()
        if not stripped:
            raise MbError(f"{field} must contain non-empty strings", 1)
        items.append(stripped)
    return tuple(items)


@dataclass(frozen=True)
class NotePayload:
    title: str
    body: str


@dataclass(frozen=True)
class DonePayload:
    bank_revision: str
    summary: str
    decisions: tuple[str, ...]
    next_steps: tuple[str, ...]
    blockers: tuple[str, ...]
    key_files: tuple[str, ...]
    completed_items: tuple[str, ...]
    new_items: tuple[str, ...]
    note: NotePayload | None

    @classmethod
    def from_json(cls, raw: str) -> "DonePayload":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MbError("done payload must be valid JSON", 1) from exc
        if not isinstance(data, dict):
            raise MbError("done payload must be a JSON object", 1)

        bank_revision_value = data.get("bank_revision")
        if not isinstance(bank_revision_value, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            bank_revision_value,
        ):
            raise MbError("bank_revision must be a lowercase SHA-256", 1)

        summary_value = data.get("summary")
        if not isinstance(summary_value, str) or not summary_value.strip():
            raise MbError("summary must be a non-empty string", 1)

        note_value = data.get("note")
        if note_value is None:
            note = None
        elif isinstance(note_value, dict):
            title_value = note_value.get("title")
            body_value = note_value.get("body")
            if not isinstance(title_value, str) or not title_value.strip():
                raise MbError("note title and body must be non-empty", 1)
            if not isinstance(body_value, str) or not body_value.strip():
                raise MbError("note title and body must be non-empty", 1)
            note = NotePayload(
                title=title_value.strip(),
                body=body_value.strip(),
            )
        else:
            raise MbError("note must be null or an object with title and body", 1)

        return cls(
            bank_revision=bank_revision_value,
            summary=summary_value.strip(),
            decisions=_string_list(data, "decisions"),
            next_steps=_string_list(data, "next_steps"),
            blockers=_string_list(data, "blockers"),
            key_files=_string_list(data, "key_files"),
            completed_items=_string_list(data, "completed_items"),
            new_items=_string_list(data, "new_items"),
            note=note,
        )
