from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitEvidence:
    root: str
    branch: str
    head: str | None
    status: tuple[str, ...]

    def json(self) -> dict[str, object]:
        return asdict(self)


def _git(root: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout.strip()


def collect_git_evidence(root: Path) -> GitEvidence:
    branch_rc, branch = _git(root, "branch", "--show-current")
    head_rc, head = _git(root, "rev-parse", "HEAD")
    _, status = _git(root, "status", "--short")
    return GitEvidence(
        str(Path(root).resolve()),
        branch if branch_rc == 0 else "",
        head if head_rc == 0 else None,
        tuple(line for line in status.splitlines() if line),
    )
