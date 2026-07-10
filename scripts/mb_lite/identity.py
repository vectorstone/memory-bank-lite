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
    command = ["git", "-C", str(root), *args]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise MbError(f"unable to run Git: {exc}", 1) from exc
    if result.returncode != 0:
        if args == ("rev-parse", "--show-toplevel"):
            raise MbError(f"not a Git repository: {root}", 1)
        if (
            args == ("config", "--get", "remote.pushDefault")
            and result.returncode == 1
        ):
            return ""
        detail = result.stderr.strip()
        message = f"Git command failed: {' '.join(command)}"
        if detail:
            message = f"{message}: {detail}"
        raise MbError(message, 1)
    return result.stdout.strip()


def normalize_remote(value: str) -> str:
    raw = value.strip()
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
    scheme = ""
    port = None
    if scp and "://" not in raw:
        host, path = scp.group(1), scp.group(2)
    else:
        try:
            parsed = urlsplit(raw)
            host, path = parsed.hostname or "", parsed.path
            scheme = parsed.scheme.lower()
            port = parsed.port
        except ValueError as exc:
            raise MbError(f"unsupported Git remote URL: {value}", 1) from exc
    host = host.lower()
    default_ports = {"http": 80, "https": 443, "ssh": 22, "git": 9418}
    if port is not None and port != default_ports.get(scheme):
        host = f"{host}:{port}"
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
    if push_default and push_default not in remotes:
        raise MbError(
            f"remote.pushDefault is not a configured Git remote: {push_default}",
            1,
        )
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
    if selected:
        remote = _git(root, "remote", "get-url", selected)
        if not remote:
            raise MbError(f"selected Git remote has no URL: {selected}", 1)
    else:
        remote = None
    key = normalize_remote(remote) if remote else f"local:{root}"
    digest = sha256(key.encode("utf-8")).hexdigest()[:12]
    slug = _slug(key)
    return RepositoryIdentity(root, key, slug, digest, f"{slug}-{digest}", remote)
