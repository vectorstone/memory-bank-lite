import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_v2_config_catalog import V2ConfigCatalogTest


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts/mb.py"


class V2CliTest(unittest.TestCase):
    def _repo(self, root: Path, remote: str | None = None) -> Path:
        repo = root / "repo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        if remote:
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
        return repo

    def test_project_init_start_and_revision_use_vault_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = self._repo(root, "git@github.com:example/repo.git")
            env = {
                **os.environ,
                "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            initialized = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "init", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            payload = json.loads(initialized.stdout)
            self.assertEqual(payload["scope_type"], "project")
            self.assertTrue(Path(payload["bank_path"]).joinpath("attention.md").is_file())
            started = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "start", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(started.stdout)["scope_id"], payload["scope_id"])
            revision = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "done", "--revision", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(len(json.loads(revision.stdout)["bank_revision"]), 64)

    def test_no_remote_second_device_requires_explicit_adopt(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            first = self._repo(root / "first")
            second = self._repo(root / "second")
            base = {**os.environ, "MB_LITE_VAULT_PATH": str(vault), "XDG_CONFIG_HOME": str(root / "config")}
            one = subprocess.run(
                ["python3", str(CLI), "--project-root", str(first), "init", "--json"],
                env={**base, "XDG_STATE_HOME": str(root / "state1")}, capture_output=True, text=True, check=True,
            )
            project_id = json.loads(one.stdout)["scope_id"]
            refused = subprocess.run(
                ["python3", str(CLI), "--project-root", str(second), "init", "--adopt", project_id],
                env={**base, "XDG_STATE_HOME": str(root / "state2")}, capture_output=True, text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
            adopted = subprocess.run(
                ["python3", str(CLI), "--project-root", str(second), "init", "--adopt", project_id, "--confirm-adopt", "--json"],
                env={**base, "XDG_STATE_HOME": str(root / "state2")}, capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(adopted.stdout)["scope_id"], project_id)


if __name__ == "__main__":
    unittest.main()
