import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest


def _repo(root: Path, name: str, remote: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", remote], check=True)
    return repo


class V2RecallVisibilityTest(unittest.TestCase):
    def _env(self, root: Path, vault: Path, *, override: bool = True) -> dict[str, str]:
        env = {
            **os.environ,
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
        }
        if override:
            env["MB_LITE_VAULT_PATH"] = str(vault)
        else:
            env.pop("MB_LITE_VAULT_PATH", None)
        return env

    def _run(self, args: list[str], repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(CLI), "--project-root", str(repo), *args],
            env=env, capture_output=True, text=True,
        )

    def _init(self, repo: Path, env: dict[str, str]) -> dict:
        result = self._run(["init", "--json"], repo, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_config_show_reports_the_vault_and_its_source(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            env = self._env(root, vault)
            shown = self._run(["config", "show", "--json"], root, env)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            payload = json.loads(shown.stdout)
            self.assertEqual(Path(payload["vault_path"]), vault.resolve())
            self.assertEqual(payload["source"], "MB_LITE_VAULT_PATH")

            file_env = self._env(root, vault, override=False)
            configured = self._run(["config", "set-vault", str(vault), "--json"], root, file_env)
            self.assertEqual(configured.returncode, 0, configured.stderr)
            shown = self._run(["config", "show", "--json"], root, file_env)
            payload = json.loads(shown.stdout)
            self.assertEqual(payload["source"], "config.json")
            self.assertEqual(
                Path(payload["config_path"]),
                root / "config" / "memory-bank-lite" / "config.json",
            )

    def test_empty_recall_is_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = _repo(root, "service", "git@github.com:example/service.git")
            env = self._env(root, vault)
            self._init(repo, env)
            result = self._run(["recall", "zzzznotpresent"], repo, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No recall matches", result.stdout)

    def test_all_banks_recall_finds_another_projects_bank(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            first = _repo(root, "service-a", "git@github.com:example/service-a.git")
            second = _repo(root, "service-b", "git@github.com:example/service-b.git")
            env = self._env(root, vault)
            self._init(first, env)
            second_payload = self._init(second, env)
            second_bank = next(
                (vault / "memory-bank/projects").glob("service-b-*/.memory-bank")
            )
            (second_bank / "notes/cross.md").write_text(
                "# Cross Bank\n\nunicorn protocol\n", encoding="utf-8"
            )

            local = self._run(["recall", "unicorn protocol"], first, env)
            self.assertIn("No recall matches", local.stdout)

            across = self._run(["recall", "unicorn protocol", "--all-banks", "--json"], first, env)
            results = json.loads(across.stdout)
            self.assertTrue(
                any(
                    result["path"] == "notes/cross.md"
                    and result["scope_id"] == second_payload["scope_id"]
                    for result in results
                ),
                results,
            )

            text = self._run(["recall", "unicorn protocol", "--all-banks"], first, env)
            self.assertIn(second_payload["scope_id"], text.stdout)

    def test_expand_is_bounded_and_points_at_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = _repo(root, "service", "git@github.com:example/service.git")
            env = self._env(root, vault)
            self._init(repo, env)
            bank = next((vault / "memory-bank/projects").glob("service-*/.memory-bank"))
            note = bank / "notes/big.md"
            note.write_text("# Big\n" + "filler token\n" * 2000, encoding="utf-8")

            found = self._run(["recall", "filler token", "--json"], repo, env)
            result = next(
                item for item in json.loads(found.stdout) if item["path"] == "notes/big.md"
            )
            expanded = self._run(["recall", "--expand", result["result_id"]], repo, env)
            self.assertEqual(expanded.returncode, 0, expanded.stderr)
            self.assertLessEqual(len(expanded.stdout.encode("utf-8")), 8192)
            self.assertIn("truncated", expanded.stdout)
            self.assertIn(str(note), expanded.stdout)


if __name__ == "__main__":
    unittest.main()
