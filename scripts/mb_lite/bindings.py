from dataclasses import dataclass
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .errors import MbError


@dataclass(frozen=True)
class Binding:
    scope_type: str
    scope_id: str


class BindingStore:
    def __init__(self, state_root: Path) -> None:
        self.root = Path(state_root)
        self.path = self.root / "bindings.json"
        self.lock_path = self.root / ".bindings.lock"

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.lock_path.is_symlink():
            raise MbError("bindings lock must not be a symlink", 4)
        descriptor = os.open(
            self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    @staticmethod
    def empty() -> dict[str, object]:
        return {"schema_version": 1, "roots": {}}

    def load(self, allow_missing: bool = True) -> dict[str, object]:
        try:
            if self.path.is_symlink():
                raise MbError("bindings file must not be a symlink", 4)
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if allow_missing:
                return self.empty()
            raise MbError("local bindings are not initialized", 2)
        except MbError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MbError(f"invalid local bindings: {exc}", 4) from exc
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "roots"} or raw.get("schema_version") != 1 or not isinstance(raw.get("roots"), dict):
            raise MbError("invalid local bindings schema", 4)
        for root, value in raw["roots"].items():
            if not isinstance(root, str) or not Path(root).is_absolute() or not isinstance(value, dict) or set(value) != {"scope_type", "scope_id"} or value.get("scope_type") not in {"project", "workspace"} or not isinstance(value.get("scope_id"), str):
                raise MbError("invalid local bindings schema", 4)
        return raw

    def _write(self, data: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".bindings.", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = ""
            parent = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except OSError as exc:
            raise MbError(f"unable to write local bindings: {exc}", 4) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def bind(self, root: Path, scope_type: str, scope_id: str) -> None:
        if scope_type not in {"project", "workspace"}:
            raise MbError(f"invalid scope type: {scope_type}", 1)
        try:
            canonical = str(Path(root).resolve(strict=True))
        except OSError as exc:
            raise MbError(f"binding root does not exist: {root}", 1) from exc
        with self.locked():
            data = self.load()
            roots = data["roots"]
            assert isinstance(roots, dict)
            existing = roots.get(canonical)
            proposed = {"scope_type": scope_type, "scope_id": scope_id}
            if existing is not None and existing != proposed:
                raise MbError(f"local root is already bound: {canonical}", 1)
            roots[canonical] = proposed
            self._write(data)

    def rebind(self, root: Path, expected: Binding, replacement: Binding) -> None:
        if expected.scope_type not in {"project", "workspace"}:
            raise MbError(f"invalid expected scope type: {expected.scope_type}", 1)
        if replacement.scope_type not in {"project", "workspace"}:
            raise MbError(f"invalid replacement scope type: {replacement.scope_type}", 1)
        try:
            canonical = str(Path(root).resolve(strict=True))
        except OSError as exc:
            raise MbError(f"binding root does not exist: {root}", 1) from exc
        with self.locked():
            data = self.load()
            roots = data["roots"]
            assert isinstance(roots, dict)
            current = roots.get(canonical)
            expected_value = {
                "scope_type": expected.scope_type,
                "scope_id": expected.scope_id,
            }
            if current != expected_value:
                raise MbError(f"local root binding changed before replacement: {canonical}", 3)
            roots[canonical] = {
                "scope_type": replacement.scope_type,
                "scope_id": replacement.scope_id,
            }
            self._write(data)

    def lookup_exact(self, root: Path) -> Binding | None:
        canonical = str(Path(root).resolve())
        raw = self.load()["roots"]
        assert isinstance(raw, dict)
        value = raw.get(canonical)
        if value is None:
            return None
        assert isinstance(value, dict)
        return Binding(str(value["scope_type"]), str(value["scope_id"]))

    def roots_for_scope(self, scope_type: str, scope_id: str) -> tuple[Path, ...]:
        raw = self.load()["roots"]
        assert isinstance(raw, dict)
        return tuple(
            Path(root)
            for root, value in sorted(raw.items())
            if isinstance(value, dict)
            and value.get("scope_type") == scope_type
            and value.get("scope_id") == scope_id
        )
