import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest
from tests.test_v2_publish import V2PublishTest


class V2DoctorRecallTest(unittest.TestCase):
    def test_doctor_reports_missing_catalog_without_scope_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            env = {
                **os.environ, "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
            }
            before = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(root), "doctor", "--json"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 4)
            payload = json.loads(result.stdout)
            self.assertIn("catalog-invalid", {item["check_id"] for item in payload["findings"]})
            after = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
            self.assertEqual(before, after)

    def test_published_knowledge_is_recalled_and_doctor_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            helper = V2PublishTest()
            vault, repo, env, _ = helper._fixture(root)
            payload = helper._payload(repo, env, "Durable Architecture")
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            recalled = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "recall", "Durable Architecture", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            results = json.loads(recalled.stdout)
            self.assertTrue(any(result["layer"] == "project-knowledge" for result in results))
            before = {
                path.relative_to(vault).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in vault.rglob("*") if path.is_file()
            }
            doctor = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "doctor", "--json"],
                env=env, capture_output=True, text=True,
            )
            self.assertIn(doctor.returncode, {0, 4})
            self.assertIn("findings", json.loads(doctor.stdout))
            after = {
                path.relative_to(vault).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in vault.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)

    def test_incomplete_transaction_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            helper = V2PublishTest()
            _, repo, env, _ = helper._fixture(root)
            incomplete = Path(env["XDG_STATE_HOME"]) / "memory-bank-lite/transactions/incomplete"
            incomplete.mkdir(parents=True)
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "doctor", "--json"],
                env=env, capture_output=True, text=True,
            )
            payload = json.loads(result.stdout)
            self.assertTrue(
                any(item["check_id"] == "transaction-incomplete" for item in payload["findings"])
            )


if __name__ == "__main__":
    unittest.main()
