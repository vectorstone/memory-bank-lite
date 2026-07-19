from dataclasses import dataclass
import re
from pathlib import Path

from .errors import MbError


@dataclass(frozen=True)
class SensitiveFinding:
    kind: str
    excerpt: str


PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential-url", re.compile(r"https?://[^\s/:]+:[^\s/@]+@[^\s]+")),
    ("password-assignment", re.compile(r"(?im)^\s*(?:password|passwd|secret|api[_-]?key)\s*[:=]\s*[^\s]{8,}\s*$")),
)

FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "auth.json"}


def scan_text(value: str) -> tuple[SensitiveFinding, ...]:
    findings: list[SensitiveFinding] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(value):
            excerpt = match.group(0)
            if len(excerpt) > 40:
                excerpt = excerpt[:16] + "..." + excerpt[-8:]
            findings.append(SensitiveFinding(kind, excerpt))
    return tuple(findings)


def enforce_safe_text(value: str, allow_sensitive: bool = False) -> None:
    findings = scan_text(value)
    if findings and not allow_sensitive:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise MbError(
            f"sensitive content blocked ({kinds}); review it or explicitly use --allow-sensitive once",
            1,
        )


def forbidden_evidence_path(value: str) -> bool:
    path = Path(value)
    lowered = {part.casefold() for part in path.parts}
    return path.name.casefold() in FORBIDDEN_NAMES or bool(
        lowered & {".ssh", ".gnupg", "secrets", "credentials"}
    )
