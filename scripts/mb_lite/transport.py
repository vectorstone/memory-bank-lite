from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .errors import MbError
from .vault import contained_path


@dataclass(frozen=True)
class VaultTransport:
    vault: Path
    preferred: str
    fallback_chain: tuple[str, ...]
    cli_binary: str | None

    @classmethod
    def load(cls, vault: Path) -> "VaultTransport":
        path = Path(vault) / ".vault-meta/transport.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError("unable to read vault transport", 4) from exc
        preferred = raw.get("preferred")
        chain = raw.get("fallback_chain")
        if not isinstance(preferred, str) or not isinstance(chain, list) or not all(isinstance(item, str) for item in chain):
            raise MbError("invalid vault transport schema", 4)
        available = raw.get("available", {})
        cli = available.get("cli", {}) if isinstance(available, dict) else {}
        binary = cli.get("binary") if isinstance(cli, dict) and cli.get("present") is True else None
        if binary is not None and not isinstance(binary, str):
            binary = None
        return cls(Path(vault).resolve(), preferred, tuple(chain), binary)

    def selected(self) -> str:
        for item in (self.preferred, *self.fallback_chain):
            if item == "cli" and self.cli_binary and shutil.which(self.cli_binary):
                return "cli"
            if item == "filesystem":
                return "filesystem"
        raise MbError("no declared vault write transport is available", 4)

    def read(self, relative: str) -> bytes | None:
        path = contained_path(self.vault, relative)
        if not path.exists():
            return None
        if not path.is_file():
            raise MbError(f"vault target is not a regular file: {relative}", 4)
        return path.read_bytes()

    def write(self, relative: str, content: bytes) -> None:
        path = contained_path(self.vault, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        selected = self.selected()
        cli_failed = False
        if selected == "cli":
            assert self.cli_binary is not None
            result = subprocess.run(
                [self.cli_binary, "write", str(self.vault), relative],
                input=content,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                cli_failed = True
                if "filesystem" not in self.fallback_chain:
                    raise MbError(
                        f"Obsidian CLI write failed for {relative}: {result.stderr.decode(errors='replace').strip()}",
                        4,
                    )
        if selected == "filesystem" or cli_failed:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                temporary = ""
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if temporary:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
        observed = self.read(relative)
        if observed != content:
            raise MbError(f"vault write verification failed: {relative}", 4)

    def remove(self, relative: str) -> None:
        path = contained_path(self.vault, relative)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
