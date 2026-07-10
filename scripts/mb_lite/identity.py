from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .errors import MbError


@dataclass(frozen=True)
class RepositoryIdentity:
    root: Path
    key: str
    slug: str
    digest: str
    project_id: str
    remote: str | None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if args == ("rev-parse", "--show-toplevel"):
            raise MbError(f"not a Git repository: {root}", 1)
        return ""
    return result.stdout.strip()


def normalize_remote(value: str) -> str:
    raw = value.strip()
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
    if scp and "://" not in raw:
        host, path = scp.group(1), scp.group(2)
    else:
        try:
            parsed = urlsplit(raw)
            host, path = parsed.hostname or "", parsed.path
        except ValueError as exc:
            raise MbError(f"unsupported Git remote URL: {value}", 1) from exc
    host = host.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not host or not path:
        raise MbError(f"unsupported Git remote URL: {value}", 1)
    return f"{host}/{path}"


def _slug(key: str) -> str:
    tail = key.rsplit("/", 1)[-1]
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.").lower()
    return value or "project"


def resolve_identity(project_root: Path) -> RepositoryIdentity:
    root = Path(_git(project_root, "rev-parse", "--show-toplevel")).resolve()
    push_default = _git(root, "config", "--get", "remote.pushDefault")
    remotes = [line for line in _git(root, "remote").splitlines() if line]
    if push_default:
        selected = push_default
    elif "origin" in remotes:
        selected = "origin"
    elif len(remotes) == 1:
        selected = remotes[0]
    elif len(remotes) > 1:
        raise MbError(
            "multiple Git remotes; configure remote.pushDefault or origin",
            1,
        )
    else:
        selected = ""
    remote = _git(root, "remote", "get-url", selected) if selected else None
    key = normalize_remote(remote) if remote else f"local:{root}"
    digest = sha256(key.encode("utf-8")).hexdigest()[:12]
    slug = _slug(key)
    return RepositoryIdentity(root, key, slug, digest, f"{slug}-{digest}", remote)
