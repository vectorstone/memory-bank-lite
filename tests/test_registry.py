import json
import multiprocessing
import os
from pathlib import Path
import queue as queue_module
import tempfile
import unittest
from unittest.mock import ANY, call, patch

from scripts.mb_lite.errors import MbError
from scripts.mb_lite.identity import RepositoryIdentity
from scripts.mb_lite.registry import RegistryStore


def _register_worker(home: str, root: str, queue) -> None:
    identity = RepositoryIdentity(
        root=Path(root),
        key="github.com/example/onduty",
        slug="onduty",
        digest="122d9ee9bd04",
        project_id="onduty-122d9ee9bd04",
        remote="git@github.com:example/onduty.git",
    )
    try:
        RegistryStore(Path(home)).register(identity)
    except MbError as exc:
        queue.put(exc.exit_code)
    else:
        queue.put(0)


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_home.cleanup)
        self.home = Path(self.temporary_home.name)
        self.identity = RepositoryIdentity(
            root=Path("/tmp/onduty"),
            key="github.com/example/onduty",
            slug="onduty",
            digest="122d9ee9bd04",
            project_id="onduty-122d9ee9bd04",
            remote="git@github.com:example/onduty.git",
        )
        self.store = RegistryStore(self.home)

    def _registry_bytes(self, projects: dict[str, object]) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "projects_root": str(self.store.projects_root),
                "projects": projects,
            },
            sort_keys=True,
        ).encode("utf-8")

    def test_register_is_idempotent_and_tracks_known_root(self) -> None:
        first = self.store.register(self.identity)
        second = self.store.register(self.identity)

        self.assertEqual(first, second)
        registry = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(
            registry["projects"][self.identity.key]["known_roots"],
            ["/tmp/onduty"],
        )

    def test_register_rejects_escaping_identity_before_write(self) -> None:
        escaping = RepositoryIdentity(
            root=self.identity.root,
            key=self.identity.key,
            slug=self.identity.slug,
            digest=self.identity.digest,
            project_id="../escape",
            remote=self.identity.remote,
        )

        with self.assertRaisesRegex(MbError, "outside projects_root") as raised:
            self.store.register(escaping)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertFalse(self.store.path.exists())

    def test_adopt_validates_new_entry_before_write(self) -> None:
        self.store.register(self.identity)
        original = self.store.path.read_bytes()
        invalid = RepositoryIdentity(
            root=Path("/tmp/onduty-renamed"),
            key="",
            slug="onduty-renamed",
            digest="abcdef123456",
            project_id="onduty-renamed-abcdef123456",
            remote="git@github.com:example/onduty-renamed.git",
        )

        with self.assertRaisesRegex(
            MbError,
            "corrupt registry schema",
        ) as raised:
            self.store.adopt(invalid, self.identity.key)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_register_returns_committed_entry_without_lookup(self) -> None:
        with patch.object(
            self.store,
            "lookup",
            side_effect=AssertionError("register called lookup"),
        ):
            entry = self.store.register(self.identity)

        self.assertEqual(entry.project_id, self.identity.project_id)
        self.assertEqual(entry.known_roots, ("/tmp/onduty",))

    def test_adopt_returns_committed_entry_without_lookup(self) -> None:
        old_entry = self.store.register(self.identity)
        changed = RepositoryIdentity(
            root=Path("/tmp/onduty-renamed"),
            key="github.com/example/onduty-renamed",
            slug="onduty-renamed",
            digest="abcdef123456",
            project_id="onduty-renamed-abcdef123456",
            remote="git@github.com:example/onduty-renamed.git",
        )

        with patch.object(
            self.store,
            "lookup",
            side_effect=AssertionError("adopt called lookup"),
        ):
            entry = self.store.adopt(changed, self.identity.key)

        self.assertEqual(entry.bank_path, old_entry.bank_path)
        self.assertEqual(
            entry.known_roots,
            ("/tmp/onduty", "/tmp/onduty-renamed"),
        )

    def test_corrupt_registry_is_never_overwritten(self) -> None:
        corrupt = b"{broken"
        self.store.path.write_bytes(corrupt)

        with self.assertRaisesRegex(MbError, "corrupt registry"):
            self.store.register(self.identity)

        self.assertEqual(self.store.path.read_bytes(), corrupt)

    def test_dangling_registry_symlink_is_never_replaced(self) -> None:
        target = self.home / "missing-registry.json"
        self.store.path.symlink_to(target)
        original_link = os.readlink(self.store.path)

        with self.assertRaisesRegex(MbError, "corrupt registry") as raised:
            self.store.register(self.identity)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertTrue(self.store.path.is_symlink())
        self.assertEqual(os.readlink(self.store.path), original_link)
        self.assertFalse(target.exists())

    def test_existing_target_registry_symlink_is_never_replaced(self) -> None:
        target = self.home / "target-registry.json"
        target_bytes = self._registry_bytes({})
        target.write_bytes(target_bytes)
        self.store.path.symlink_to(target)
        original_link = os.readlink(self.store.path)

        with self.assertRaisesRegex(MbError, "corrupt registry") as raised:
            self.store.register(self.identity)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertTrue(self.store.path.is_symlink())
        self.assertEqual(os.readlink(self.store.path), original_link)
        self.assertEqual(target.read_bytes(), target_bytes)

    def test_registry_symlink_swap_after_lstat_fails_closed(self) -> None:
        self.store.register(self.identity)
        original = self.store.path.read_bytes()
        target = self.home / "swapped-registry.json"
        target.write_bytes(original)
        changed = RepositoryIdentity(
            root=Path("/tmp/onduty-renamed"),
            key="github.com/example/onduty-renamed",
            slug="onduty-renamed",
            digest="abcdef123456",
            project_id="onduty-renamed-abcdef123456",
            remote="git@github.com:example/onduty-renamed.git",
        )
        operations = {
            "lookup": lambda: self.store.lookup(self.identity.key),
            "register": lambda: self.store.register(self.identity),
            "adopt": lambda: self.store.adopt(changed, self.identity.key),
        }
        real_lstat = Path.lstat

        for name, operation in operations.items():
            with self.subTest(name=name):
                if os.path.lexists(self.store.path):
                    self.store.path.unlink()
                self.store.path.write_bytes(original)
                swapped = False

                def swap_after_lstat(path):
                    nonlocal swapped
                    registry_stat = real_lstat(path)
                    if not swapped and Path(path) == self.store.path:
                        self.store.path.unlink()
                        self.store.path.symlink_to(target)
                        swapped = True
                    return registry_stat

                with patch(
                    "scripts.mb_lite.registry.Path.lstat",
                    autospec=True,
                    side_effect=swap_after_lstat,
                ):
                    with self.assertRaises(MbError) as raised:
                        operation()

                self.assertEqual(raised.exception.exit_code, 4)
                self.assertTrue(swapped)
                self.assertTrue(self.store.path.is_symlink())
                self.assertEqual(target.read_bytes(), original)

    def test_write_fsyncs_file_and_parent_directory(self) -> None:
        directory_descriptor = 90210
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        real_open = os.open

        def open_path(path, flags, *args):
            if Path(path).resolve() == self.store.home:
                return directory_descriptor
            return real_open(path, flags, *args)

        with (
            patch("scripts.mb_lite.registry.os.fsync") as fsync,
            patch("scripts.mb_lite.registry.os.replace") as replace,
            patch(
                "scripts.mb_lite.registry.os.open",
                side_effect=open_path,
            ) as open_directory,
            patch("scripts.mb_lite.registry.os.close") as close_directory,
        ):
            self.store._write(self.store._empty())

        replace.assert_called_once()
        self.assertIn(
            call(self.store.home, directory_flags),
            open_directory.call_args_list,
        )
        self.assertEqual(fsync.call_args_list, [call(ANY), call(directory_descriptor)])
        close_directory.assert_called_once_with(directory_descriptor)

    def test_write_closes_directory_when_directory_fsync_fails(self) -> None:
        directory_descriptor = 90210
        real_open = os.open

        def open_path(path, flags, *args):
            if Path(path).resolve() == self.store.home:
                return directory_descriptor
            return real_open(path, flags, *args)

        with (
            patch(
                "scripts.mb_lite.registry.os.fsync",
                side_effect=[None, OSError("directory fsync failed")],
            ),
            patch("scripts.mb_lite.registry.os.replace"),
            patch(
                "scripts.mb_lite.registry.os.open",
                side_effect=open_path,
            ),
            patch("scripts.mb_lite.registry.os.close") as close_directory,
        ):
            with self.assertRaisesRegex(MbError, "unable to write registry") as raised:
                self.store._write(self.store._empty())

        self.assertEqual(raised.exception.exit_code, 4)
        close_directory.assert_called_once_with(directory_descriptor)

    def test_replace_failure_preserves_registry_and_cleans_temp_file(self) -> None:
        self.store.register(self.identity)
        original = self.store.path.read_bytes()
        clone = RepositoryIdentity(
            root=Path("/tmp/onduty-clone"),
            key=self.identity.key,
            slug=self.identity.slug,
            digest=self.identity.digest,
            project_id=self.identity.project_id,
            remote=self.identity.remote,
        )

        with patch(
            "scripts.mb_lite.registry.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(MbError, "unable to write registry") as raised:
                self.store.register(clone)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.home.glob(".registry.*.tmp")), [])

    def test_invalid_top_level_schema_is_never_overwritten(self) -> None:
        projects_root = str(self.store.projects_root)
        cases = {
            "missing_projects_root": {
                "version": 1,
                "projects": {},
            },
            "mismatched_projects_root": {
                "version": 1,
                "projects_root": "/tmp/other/projects",
                "projects": {},
            },
            "non_string_projects_root": {
                "version": 1,
                "projects_root": 7,
                "projects": {},
            },
            "extra_key": {
                "version": 1,
                "projects_root": projects_root,
                "projects": {},
                "unexpected": True,
            },
            "boolean_version": {
                "version": True,
                "projects_root": projects_root,
                "projects": {},
            },
        }

        for name, registry in cases.items():
            with self.subTest(name=name):
                original = json.dumps(registry, sort_keys=True).encode("utf-8")
                self.store.path.write_bytes(original)

                with self.assertRaisesRegex(
                    MbError,
                    "corrupt registry schema",
                ) as raised:
                    self.store.register(self.identity)

                self.assertEqual(raised.exception.exit_code, 4)
                self.assertEqual(self.store.path.read_bytes(), original)

    def test_invalid_unrelated_entry_is_never_overwritten(self) -> None:
        valid_entry = {
            "project_id": "unrelated-123456789abc",
            "remote": None,
            "bank_path": str(
                self.store.projects_root
                / "unrelated-123456789abc"
                / ".memory-bank"
            ),
            "known_roots": ["/tmp/unrelated"],
        }
        cases = {
            "non_dict_entry": "broken",
            "empty_project_id": {**valid_entry, "project_id": ""},
            "empty_remote": {**valid_entry, "remote": ""},
            "empty_bank_path": {**valid_entry, "bank_path": ""},
            "empty_known_root": {**valid_entry, "known_roots": [""]},
            "extra_entry_key": {**valid_entry, "unexpected": True},
            "escaped_bank_path": {
                **valid_entry,
                "bank_path": "/tmp/escape/.memory-bank",
            },
        }

        for name, invalid_entry in cases.items():
            with self.subTest(name=name):
                registry = {
                    "version": 1,
                    "projects_root": str(self.store.projects_root),
                    "projects": {"github.com/example/unrelated": invalid_entry},
                }
                original = json.dumps(registry, sort_keys=True).encode("utf-8")
                self.store.path.write_bytes(original)

                with self.assertRaises(MbError) as raised:
                    self.store.register(self.identity)

                self.assertEqual(raised.exception.exit_code, 4)
                self.assertEqual(self.store.path.read_bytes(), original)

    def test_nul_bank_path_is_rejected_without_overwrite(self) -> None:
        registry = {
            "version": 1,
            "projects_root": str(self.store.projects_root),
            "projects": {
                "github.com/example/unrelated": {
                    "project_id": "unrelated-123456789abc",
                    "remote": None,
                    "bank_path": "\0",
                    "known_roots": ["/tmp/unrelated"],
                }
            },
        }
        original = json.dumps(registry, sort_keys=True).encode("utf-8")
        self.store.path.write_bytes(original)

        with self.assertRaisesRegex(
            MbError,
            "corrupt registry schema",
        ) as raised:
            self.store.register(self.identity)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_relative_bank_path_is_rejected_independent_of_cwd(self) -> None:
        registry = self._registry_bytes(
            {
                "github.com/example/unrelated": {
                    "project_id": "unrelated-123456789abc",
                    "remote": None,
                    "bank_path": "unrelated-123456789abc/.memory-bank",
                    "known_roots": ["/tmp/unrelated"],
                }
            }
        )
        self.store.path.write_bytes(registry)
        self.store.projects_root.mkdir()
        original_cwd = Path.cwd()

        try:
            os.chdir(self.store.projects_root)
            with self.assertRaisesRegex(
                MbError,
                "corrupt registry schema",
            ) as raised:
                self.store.register(self.identity)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(raised.exception.exit_code, 4)
        self.assertEqual(self.store.path.read_bytes(), registry)

    def test_lookup_rejects_bank_outside_projects_root(self) -> None:
        self.store.register(self.identity)
        registry = json.loads(self.store.path.read_text(encoding="utf-8"))
        registry["projects"][self.identity.key]["bank_path"] = (
            "/tmp/escape/.memory-bank"
        )
        self.store.path.write_text(json.dumps(registry), encoding="utf-8")

        with self.assertRaisesRegex(MbError, "outside projects_root"):
            self.store.lookup(self.identity.key)

    def test_adopt_reuses_old_bank_and_preserves_old_key(self) -> None:
        old_entry = self.store.register(self.identity)
        changed = RepositoryIdentity(
            root=Path("/tmp/onduty-renamed"),
            key="github.com/example/onduty-renamed",
            slug="onduty-renamed",
            digest="abcdef123456",
            project_id="onduty-renamed-abcdef123456",
            remote="git@github.com:example/onduty-renamed.git",
        )

        adopted = self.store.adopt(changed, self.identity.key)

        self.assertEqual(adopted.bank_path, old_entry.bank_path)
        self.assertEqual(
            self.store.lookup(self.identity.key).bank_path,
            old_entry.bank_path,
        )
        self.assertEqual(
            adopted.known_roots,
            ("/tmp/onduty", "/tmp/onduty-renamed"),
        )

    def test_concurrent_registration_preserves_both_known_roots(self) -> None:
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=_register_worker,
                args=(str(self.home), f"/tmp/clone-{index}", queue),
            )
            for index in range(2)
        ]
        started_processes = []

        try:
            for process in processes:
                process.start()
                started_processes.append(process)
            for process in started_processes:
                process.join(timeout=5)

            for process in started_processes:
                self.assertFalse(
                    process.is_alive(),
                    f"registration worker {process.pid} timed out",
                )
            self.assertEqual(
                [process.exitcode for process in started_processes],
                [0, 0],
            )
            try:
                statuses = [queue.get(timeout=5) for _ in started_processes]
            except queue_module.Empty:
                self.fail("timed out waiting for registration worker result")
            self.assertEqual(sorted(statuses), [0, 0])
            self.assertEqual(
                self.store.lookup(self.identity.key).known_roots,
                ("/tmp/clone-0", "/tmp/clone-1"),
            )
        finally:
            try:
                for process in started_processes:
                    if process.is_alive():
                        process.terminate()
                for process in started_processes:
                    process.join(timeout=5)
            finally:
                try:
                    queue.close()
                finally:
                    queue.join_thread()


if __name__ == "__main__":
    unittest.main()
