"""Read-only lexical recall over Memory Bank markdown sources."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .bank_fs import bank_markdown_files, read_bank_text
from .errors import MbError


@dataclass(frozen=True)
class RecallResult:
    result_id: str
    path: str
    heading: str
    offset: int
    snippet: str


@dataclass(frozen=True)
class ScopedRecallResult:
    result_id: str
    path: str
    heading: str
    offset: int
    snippet: str
    layer: str
    scope_id: str


Document = tuple[str, str, str, str]


_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_TERM = re.compile(r"\w+", re.UNICODE)
_SOURCES = ("status.md", "attention.md", "progress.md", "lessons.md", "backlog.md")


def _files(bank: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for name in _SOURCES:
        content = read_bank_text(bank, name)
        if content is not None:
            files.append((name, content))
    for directory in ("notes", "plans", "plans/done"):
        files.extend(
            (f"{directory}/{name}", content)
            for name, content in bank_markdown_files(bank, directory)
        )
    return files


def bank_documents(bank: Path, scope_id: str) -> tuple[Document, ...]:
    return tuple(("bank", scope_id, relative, text) for relative, text in _files(bank))


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
    for relative, text in _files(bank):
        for heading, offset, body in _chunks(text, relative.rsplit("/", 1)[-1]):
            haystack = (heading + "\n" + body).casefold()
            score = sum(haystack.count(term) for term in terms)
            if score:
                results.append((score, RecallResult(_result_id(relative, heading, offset), relative, heading, offset, _snippet(heading, body))))
    results.sort(key=lambda item: (-item[0], item[1].path, item[1].offset))
    return [result for _, result in results[:10]]


def expand_result(bank: Path, result_id: str) -> RecallResult:
    for relative, text in _files(bank):
        for heading, offset, body in _chunks(text, relative.rsplit("/", 1)[-1]):
            if _result_id(relative, heading, offset) == result_id:
                return RecallResult(
                    result_id,
                    relative,
                    heading,
                    offset,
                    body.rstrip(),
                )
    raise MbError(f"unknown recall result: {result_id}", 1)


def scoped_search(scopes: tuple[tuple[str, Path], ...], query: str) -> list[ScopedRecallResult]:
    documents = tuple(document for scope_id, bank in scopes for document in bank_documents(bank, scope_id))
    return document_search(documents, query)


def scoped_expand(scopes: tuple[tuple[str, Path], ...], result_id: str) -> ScopedRecallResult:
    documents = tuple(document for scope_id, bank in scopes for document in bank_documents(bank, scope_id))
    return document_expand(documents, result_id)


def _all_results(bank: Path) -> list[RecallResult]:
    results: list[RecallResult] = []
    for relative, text in _files(bank):
        for heading, offset, body in _chunks(text, relative.rsplit("/", 1)[-1]):
            results.append(
                RecallResult(_result_id(relative, heading, offset), relative, heading, offset, _snippet(heading, body))
            )
    return results


def _document_id(layer: str, scope_id: str, path: str, heading: str, offset: int) -> str:
    return hashlib.sha256(
        f"{layer}\0{scope_id}\0{path}\0{heading}\0{offset}".encode("utf-8")
    ).hexdigest()[:12]


def document_search(documents: tuple[Document, ...], query: str) -> list[ScopedRecallResult]:
    terms = [term.casefold() for term in _TERM.findall(query) if len(term) >= 2]
    if not terms:
        raise MbError("query must contain a term of at least two characters", 1)
    matches: list[tuple[int, ScopedRecallResult]] = []
    for layer, scope_id, path, text in documents:
        for heading, offset, body in _chunks(text, path.rsplit("/", 1)[-1]):
            haystack = (heading + "\n" + body).casefold()
            score = sum(haystack.count(term) for term in terms)
            if score:
                matches.append(
                    (
                        score,
                        ScopedRecallResult(
                            _document_id(layer, scope_id, path, heading, offset),
                            path,
                            heading,
                            offset,
                            _snippet(heading, body),
                            layer,
                            scope_id,
                        ),
                    )
                )
    matches.sort(
        key=lambda item: (
            -item[0], item[1].layer, item[1].scope_id, item[1].path, item[1].offset
        )
    )
    return [result for _, result in matches[:10]]


def document_expand(documents: tuple[Document, ...], result_id: str) -> ScopedRecallResult:
    for layer, scope_id, path, text in documents:
        for heading, offset, body in _chunks(text, path.rsplit("/", 1)[-1]):
            candidate = _document_id(layer, scope_id, path, heading, offset)
            if candidate == result_id:
                return ScopedRecallResult(
                    candidate, path, heading, offset, body.rstrip(), layer, scope_id
                )
    raise MbError(f"unknown recall result: {result_id}", 1)


recall = search
expand = expand_result
