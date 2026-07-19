import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.mb_lite.catalog import CatalogStore
from scripts.mb_lite.compound import global_lock
from scripts.mb_lite.errors import MbError
from scripts.mb_lite.publish import apply_publish_plan, build_publish_plan
from scripts.mb_lite.transport import VaultTransport
from tests.test_v2_config_catalog import V2ConfigCatalogTest
from tests.test_v2_publish import V2PublishTest


class V2TransportRecoveryTest(unittest.TestCase):
    def test_cli_transport_writes_and_verifies_content(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            binary = root / "bin/fake-obsidian-cli"
            binary.parent.mkdir()
            binary.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = write ] || exit 2\n"
                "mkdir -p \"$2/$(dirname \"$3\")\"\n"
                "cat > \"$2/$3\"\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            (vault / ".vault-meta/transport.json").write_text(json.dumps({
                "preferred": "cli", "fallback_chain": ["cli", "filesystem"],
                "available": {"cli": {"present": True, "binary": binary.name}},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"PATH": str(binary.parent) + os.pathsep + os.environ["PATH"]}):
                transport = VaultTransport.load(vault)
                self.assertEqual(transport.selected(), "cli")
                transport.write("wiki/sources/cli-test.md", b"# CLI Test\n")
            self.assertEqual((vault / "wiki/sources/cli-test.md").read_bytes(), b"# CLI Test\n")

    def test_declared_filesystem_fallback_is_used_when_cli_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            vault = V2ConfigCatalogTest()._vault(Path(value))
            (vault / ".vault-meta/transport.json").write_text(json.dumps({
                "preferred": "cli", "fallback_chain": ["cli", "filesystem"],
                "available": {"cli": {"present": True, "binary": "definitely-missing-cli"}},
            }), encoding="utf-8")
            transport = VaultTransport.load(vault)
            self.assertEqual(transport.selected(), "filesystem")
            transport.write("wiki/sources/fallback.md", b"fallback\n")
            self.assertEqual((vault / "wiki/sources/fallback.md").read_text(), "fallback\n")

    def test_cli_runtime_failure_falls_back_when_declared(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            binary = root / "bin/failing-cli"
            binary.parent.mkdir()
            binary.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
            binary.chmod(0o755)
            (vault / ".vault-meta/transport.json").write_text(json.dumps({
                "preferred": "cli", "fallback_chain": ["cli", "filesystem"],
                "available": {"cli": {"present": True, "binary": binary.name}},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"PATH": str(binary.parent) + os.pathsep + os.environ["PATH"]}):
                transport = VaultTransport.load(vault)
                transport.write("wiki/sources/runtime-fallback.md", b"recovered\n")
            self.assertEqual((vault / "wiki/sources/runtime-fallback.md").read_text(), "recovered\n")

    def test_publish_failure_rolls_back_every_applied_file(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            helper = V2PublishTest()
            vault, repo, env, _ = helper._fixture(root)
            raw = json.dumps(helper._payload(repo, env))
            catalog = CatalogStore(vault)
            plan = build_publish_plan(
                vault,
                catalog,
                raw,
                allowed_scope_ids=frozenset(record.project_id for record in catalog.projects()),
                roots_for_scope=lambda scope_type, scope_id: (),
            )
            before = {
                relative: (vault / relative).read_bytes() if (vault / relative).exists() else None
                for relative in plan.outputs
            }
            original_write = VaultTransport.write
            calls = {"count": 0, "failed": False}

            def fail_once(transport, relative, content):
                calls["count"] += 1
                if calls["count"] == 2 and not calls["failed"]:
                    calls["failed"] = True
                    original_write(transport, relative, content)
                    raise MbError("injected transport failure", 4)
                return original_write(transport, relative, content)

            with mock.patch.object(VaultTransport, "write", new=fail_once):
                with self.assertRaisesRegex(MbError, "injected transport failure"):
                    apply_publish_plan(plan, vault, root / "state")
            after = {
                relative: (vault / relative).read_bytes() if (vault / relative).exists() else None
                for relative in plan.outputs
            }
            self.assertEqual(before, after)
            completion = json.loads(
                (root / "state/transactions" / plan.transaction_id / "complete.json").read_text(encoding="utf-8")
            )
            self.assertEqual(completion["status"], "rolled_back")
            with global_lock(root / "state"):
                pass


if __name__ == "__main__":
    unittest.main()
