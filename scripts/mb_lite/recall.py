"""Read-only lexical recall over Memory Bank markdown sources."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .errors import MbError


@dataclass(frozen=True)
class RecallResult:
    result_id: str
    path: str
    heading: str
    offset: int
    snippet: str


_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_TERM = re.compile(r"\w+", re.UNICODE)
_SOURCES = ("progress.md", "backlog.md")


def _files(bank: Path) -> list[Path]:
    bank = bank.resolve()
    files = [bank / name for name in _SOURCES]
    for directory in (bank / "notes", bank / "plans", bank / "plans" / "done"):
        if directory.is_dir() and not directory.is_symlink():
            files.extend(sorted(directory.glob("*.md")))
    accepted = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            path.resolve().relative_to(bank)
        except ValueError:
            continue
        accepted.append(path)
    return accepted


def _chunks(text: str, filename: str) -> list[tuple[str, int, str]]:
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(filename, 0, text)]
    chunks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start():end]
        chunks.append((match.group(1).strip(), match.start(), body))
    return chunks


def _snippet(heading: str, body: str) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return "\n".join(lines[:2])[:240]


def _result_id(path: str, heading: str, offset: int) -> str:
    payload = f"{path}\0{heading}\0{offset}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def search(bank: Path, query: str) -> list[RecallResult]:
    terms = [term.casefold() for term in _TERM.findall(query) if len(term) >= 2]
    if not terms:
        raise MbError("query must contain a term of at least two characters", 1)
    results: list[tuple[int, RecallResult]] = []
    bank = Path(bank).resolve()
    for path in _files(bank):
        relative = path.relative_to(bank).as_posix()
        text = path.read_text(encoding="utf-8")
        for heading, offset, body in _chunks(text, path.name):
            haystack = (heading + "\n" + body).casefold()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append((score, RecallResult(_result_id(relative, heading, offset), relative, heading, offset, _snippet(heading, body))))
    results.sort(key=lambda item: (-item[0], item[1].path, item[1].offset))
    return [result for _, result in results[:10]]


def expand_result(bank: Path, result_id: str) -> RecallResult:
    bank = Path(bank).resolve()
    for path in _files(bank):
        relative = path.relative_to(bank).as_posix()
        text = path.read_text(encoding="utf-8")
        for heading, offset, body in _chunks(text, path.name):
            if _result_id(relative, heading, offset) == result_id:
                return RecallResult(
                    result_id,
                    relative,
                    heading,
                    offset,
                    body.rstrip(),
                )
    raise MbError(f"unknown recall result: {result_id}", 1)


recall = search
expand = expand_result
