import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

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

    def test_register_is_idempotent_and_tracks_known_root(self) -> None:
        first = self.store.register(self.identity)
        second = self.store.register(self.identity)

        self.assertEqual(first, second)
        registry = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(
            registry["projects"][self.identity.key]["known_roots"],
            ["/tmp/onduty"],
        )

    def test_corrupt_registry_is_never_overwritten(self) -> None:
        corrupt = b"{broken"
        self.store.path.write_bytes(corrupt)

        with self.assertRaisesRegex(MbError, "corrupt registry"):
            self.store.register(self.identity)

        self.assertEqual(self.store.path.read_bytes(), corrupt)

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

        for process in processes:
            process.start()
        for process in processes:
            process.join()

        self.assertEqual([process.exitcode for process in processes], [0, 0])
        self.assertEqual(sorted(queue.get() for _ in processes), [0, 0])
        self.assertEqual(
            self.store.lookup(self.identity.key).known_roots,
            ("/tmp/clone-0", "/tmp/clone-1"),
        )


if __name__ == "__main__":
    unittest.main()
