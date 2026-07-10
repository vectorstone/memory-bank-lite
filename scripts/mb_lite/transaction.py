from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path

from .errors import MbError


@dataclass
class BackupTransaction:
    directory: Path
    records: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def create(cls, state_root: Path, timestamp: str) -> "BackupTransaction":
        directory = state_root / timestamp
        directory.mkdir(parents=True, exist_ok=False)
        return cls(directory)

    def capture(self, path: Path, action: str) -> None:
        if path.is_symlink():
            target = os.readlink(path)
            self.records.append(
                {
                    "path": str(path),
                    "backup": None,
                    "sha256": sha256(target.encode("utf-8")).hexdigest(),
                    "target": target,
                    "action": action,
                    "kind": "symlink",
                },
            )
            return
        data = path.read_bytes()
        name = f"{len(self.records):04d}-{path.name}"
        backup = self.directory / name
        backup.write_bytes(data)
        self.records.append(
            {
                "path": str(path),
                "backup": name,
                "sha256": sha256(data).hexdigest(),
                "action": action,
                "kind": "file",
            },
        )

    def capture_missing(self, path: Path, action: str) -> None:
        self.records.append(
            {
                "path": str(path),
                "backup": None,
                "sha256": None,
                "action": action,
                "kind": "missing",
            },
        )

    def finish(self) -> None:
        (self.directory / "manifest.json").write_text(
            json.dumps(
                {"version": 1, "records": self.records},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def rollback(directory: Path) -> None:
        try:
            data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MbError(f"invalid rollback manifest: {directory}", 4) from exc
        for record in reversed(data["records"]):
            path = Path(record["path"])
            if record["kind"] == "file":
                backup = directory / record["backup"]
                content = backup.read_bytes()
                if sha256(content).hexdigest() != record["sha256"]:
                    raise MbError(f"backup checksum mismatch: {backup}", 4)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            elif record["kind"] == "symlink":
                target = str(record["target"])
                if sha256(target.encode("utf-8")).hexdigest() != record["sha256"]:
                    raise MbError(f"backup checksum mismatch: symlink {path}", 4)
                if path.is_symlink() or path.is_file():
                    path.unlink()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target)
            elif record["kind"] == "missing" and (path.is_symlink() or path.is_file()):
                path.unlink()
