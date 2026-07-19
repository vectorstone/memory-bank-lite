import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.test_v2_cli import CLI
from tests.test_v2_publish import managed


REAL_VAULT = os.environ.get("MB_LITE_REAL_CONTRACT_VAULT")


@unittest.skipUnless(REAL_VAULT, "set MB_LITE_REAL_CONTRACT_VAULT for real contract acceptance")
class V2RealContractTest(unittest.TestCase):
    def test_disposable_copy_of_real_contract(self) -> None:
        source = Path(REAL_VAULT or "").resolve()
        relative_files = (
            "WIKI.md",
            "scripts/wiki-lock.sh",
            "scripts/wiki-mode.py",
            "scripts/allocate-address.sh",
            ".vault-meta/mode.json",
            ".vault-meta/address-counter.txt",
            "wiki/index.md",
            "wiki/log.md",
            "wiki/hot.md",
            "wiki/sources/_index.md",
            ".raw/.manifest.json",
        )
        source_hashes = {
            relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
            for relative in relative_files
            if (source / relative).is_file()
        }
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = root / "vault"
            for relative in relative_files:
                origin = source / relative
                if not origin.is_file():
                    continue
                target = vault / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
            transport = vault / ".vault-meta/transport.json"
            transport.write_text(
                json.dumps({
                    "preferred": "filesystem",
                    "fallback_chain": ["filesystem"],
                    "available": {"filesystem": {"present": True}},
                }) + "\n",
                encoding="utf-8",
            )
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/real-contract.git"],
                check=True,
            )
            env = {
                **os.environ,
                "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            def run_cli(*arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(
                    ["python3", str(CLI), "--project-root", str(repo), *arguments],
                    env=env,
                    input=input_text,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"mb {' '.join(arguments)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
                )
                return result

            run_cli("init", "--json")
            snapshot = json.loads(
                run_cli("publish", "--snapshot", "--json").stdout
            )
            item = snapshot["scopes"][0]
            title = "Real Contract Knowledge"
            payload = {
                "catalog_revision": snapshot["catalog_revision"],
                "transaction_id": snapshot["transaction_id"],
                "expected_hashes": snapshot["expected_hashes"],
                "updates": [{
                    "scope_type": "project",
                    "scope_id": item["scope_id"],
                    "bank_revision": item["bank_revision"],
                    "page_hash": item["page_hash"],
                    "title": title,
                    "summary": "Disposable real-contract acceptance",
                    "managed_markdown": managed(title),
                    "git_cursors": item["git_cursors"],
                }],
                "hot_content": (vault / "wiki/hot.md").read_text(encoding="utf-8"),
            }
            preview = run_cli("publish", "--preview", "--json", input_text=json.dumps(payload))
            self.assertFalse(json.loads(preview.stdout)["noop"])
            run_cli("publish", "--apply", "--json", input_text=json.dumps(payload))
            run_cli("doctor", "--json")
        after_hashes = {
            relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
            for relative in source_hashes
        }
        self.assertEqual(source_hashes, after_hashes)


if __name__ == "__main__":
    unittest.main()
