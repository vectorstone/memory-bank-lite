import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_v2_cli import CLI
from tests.test_v2_config_catalog import V2ConfigCatalogTest


def managed(title: str) -> str:
    headings = (
        "Purpose And Boundary", "Current Capability Boundary", "Architecture And Key Paths",
        "Stable Decisions And Rationale", "Invariants And Attention", "Verified Lessons",
        "Superseded Knowledge", "Long-Term Open Questions",
    )
    return "\n\n".join(f"## {heading}\n\n- {title}: {heading}" for heading in headings)


class V2PublishTest(unittest.TestCase):
    def _fixture(self, root: Path):
        vault = V2ConfigCatalogTest()._vault(root)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
        env = {
            **os.environ, "MB_LITE_VAULT_PATH": str(vault),
            "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
        }
        initialized = json.loads(subprocess.run(
            ["python3", str(CLI), "--project-root", str(repo), "init", "--json"],
            env=env, capture_output=True, text=True, check=True,
        ).stdout)
        return vault, repo, env, initialized

    def _payload(self, repo: Path, env: dict[str, str], title: str = "Repository Knowledge"):
        snapshot = json.loads(subprocess.run(
            ["python3", str(CLI), "--project-root", str(repo), "publish", "--snapshot", "--json"],
            env=env, capture_output=True, text=True, check=True,
        ).stdout)
        item = snapshot["scopes"][0]
        return {
            "catalog_revision": snapshot["catalog_revision"],
            "transaction_id": snapshot["transaction_id"],
            "expected_hashes": snapshot["expected_hashes"],
            "updates": [{
                "scope_type": "project", "scope_id": item["scope_id"],
                "bank_revision": item["bank_revision"], "page_hash": item["page_hash"],
                "title": title, "summary": "Long-term repository knowledge",
                "managed_markdown": managed(title),
                "git_cursors": item["git_cursors"],
            }],
            "hot_content": (Path(env["MB_LITE_VAULT_PATH"]) / "wiki/hot.md").read_text(encoding="utf-8") + "\n## Recent Changes\n\n- Published repository knowledge.\n",
        }

    def test_preview_is_read_only_and_apply_updates_all_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, initialized = self._fixture(root)
            payload = self._payload(repo, env)
            preview = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            preview_data = json.loads(preview.stdout)
            page = vault / "wiki/sources/repo-project-knowledge.md"
            self.assertFalse(page.exists())
            self.assertIn("wiki/log.md", preview_data["files"])
            applied = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            result = json.loads(applied.stdout)
            self.assertFalse(result["noop"])
            self.assertIn("mb_scope_id", page.read_text(encoding="utf-8"))
            self.assertIn(
                "[[wiki/sources/repo-project-knowledge|Repository Knowledge]]",
                (vault / "wiki/index.md").read_text(encoding="utf-8"),
            )
            self.assertIn("publish | Repository Knowledge", (vault / "wiki/log.md").read_text(encoding="utf-8"))
            state_path = vault / "memory-bank/publish-state" / f"{initialized['scope_id']}.json"
            self.assertTrue(state_path.is_file())
            self.assertNotIn(str(repo), state_path.read_text(encoding="utf-8"))

    def test_repeated_identical_publish_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _, repo, env, _ = self._fixture(root)
            first = self._payload(repo, env)
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(first), capture_output=True, text=True, check=True,
            )
            second = self._payload(repo, env)
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview", "--json"],
                env=env, input=json.dumps(second), capture_output=True, text=True, check=True,
            )
            self.assertTrue(json.loads(result.stdout)["noop"])

    def test_remote_resolved_unbound_clone_contributes_current_git_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
                check=True,
            )
            clone = root / "clone"
            subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
            subprocess.run(
                ["git", "-C", str(clone), "remote", "set-url", "origin", "git@github.com:example/repo.git"],
                check=True,
            )
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(clone), "publish", "--snapshot", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            self.assertIn(
                str(clone.resolve()),
                {item["root"] for item in snapshot["scopes"][0]["git"]},
            )

    def test_manual_managed_edit_blocks_next_publish(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            first = self._payload(repo, env)
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(first), capture_output=True, text=True, check=True,
            )
            page = vault / "wiki/sources/repo-project-knowledge.md"
            page.write_text(page.read_text(encoding="utf-8").replace("Repository Knowledge: Verified Lessons", "Human edit"), encoding="utf-8")
            second = self._payload(repo, env, "Changed Knowledge")
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview"],
                env=env, input=json.dumps(second), capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("managed knowledge was edited", result.stderr)

    def test_workspace_publish_updates_workspace_and_project_pages_together(self) -> None:
        from tests.test_v2_workspace import V2WorkspaceTest
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            workspace = root / "workspace"
            workspace.mkdir()
            helper = V2WorkspaceTest()
            helper._repo(workspace, "service-a", "git@github.com:example/service-a.git")
            helper._repo(workspace, "service-b", "git@github.com:example/service-b.git")
            env = {
                **os.environ, "MB_LITE_VAULT_PATH": str(vault),
                "XDG_STATE_HOME": str(root / "state"), "XDG_CONFIG_HOME": str(root / "config"),
            }
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "init", "--workspace", "--name", "Order Sync", "--confirm-members", "--json"],
                env=env, capture_output=True, text=True, check=True,
            )
            snapshot = json.loads(subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "publish", "--snapshot", "--json"],
                env=env, capture_output=True, text=True, check=True,
            ).stdout)
            chosen = snapshot["scopes"][:2]
            updates = []
            for item in chosen:
                title = f"{item['display_name']} Knowledge"
                updates.append({
                    "scope_type": item["scope_type"], "scope_id": item["scope_id"],
                    "bank_revision": item["bank_revision"], "page_hash": item["page_hash"],
                    "title": title, "summary": "Stable workspace knowledge",
                    "managed_markdown": managed(title), "git_cursors": item["git_cursors"],
                })
            payload = {
                "catalog_revision": snapshot["catalog_revision"],
                "transaction_id": snapshot["transaction_id"],
                "expected_hashes": snapshot["expected_hashes"],
                "updates": updates,
                "hot_content": (vault / "wiki/hot.md").read_text(encoding="utf-8") + "\n## Recent Changes\n\n- Published workspace knowledge.\n",
            }
            result = subprocess.run(
                ["python3", str(CLI), "--project-root", str(workspace), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            applied = json.loads(result.stdout)
            self.assertEqual(set(applied["changed_scopes"]), {item["scope_id"] for item in chosen})
            self.assertTrue(list((vault / "wiki/sources").glob("*workspace-knowledge.md")))
            self.assertTrue(list((vault / "wiki/sources").glob("*project-knowledge.md")))

    def test_address_is_previewed_then_reserved_only_on_apply(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            counter = vault / ".vault-meta/address-counter.txt"
            counter.write_text("1\n", encoding="utf-8")
            manifest = vault / ".raw/.manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"sources": {}, "address_map": {}}\n', encoding="utf-8")
            allocator = vault / "scripts/allocate-address.sh"
            allocator.write_text(
                "#!/bin/sh\n"
                "counter=\"$(dirname \"$0\")/../.vault-meta/address-counter.txt\"\n"
                "n=$(cat \"$counter\")\n"
                "if [ \"${1:-}\" = --peek ]; then\n"
                "  printf '%s\\n' \"$n\"\n"
                "else\n"
                "  printf '%s\\n' $((n + 1)) > \"$counter\"\n"
                "  printf 'c-%06d\\n' \"$n\"\n"
                "fi\n",
                encoding="utf-8",
            )
            allocator.chmod(0o755)
            payload = self._payload(repo, env)
            preview = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            self.assertIn("c-000001", json.loads(preview.stdout)["address_reservations"].values())
            self.assertEqual(counter.read_text(encoding="utf-8"), "1\n")
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            self.assertEqual(counter.read_text(encoding="utf-8"), "2\n")
            page = vault / "wiki/sources/repo-project-knowledge.md"
            self.assertIn('address: "c-000001"', page.read_text(encoding="utf-8"))
            address_map = json.loads(manifest.read_text(encoding="utf-8"))["address_map"]
            self.assertEqual(address_map["wiki/sources/repo-project-knowledge.md"], "c-000001")

    def test_unavailable_address_allocator_keeps_publish_addressless(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            allocator = vault / "scripts/allocate-address.sh"
            allocator.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            allocator.chmod(0o755)
            payload = self._payload(repo, env)
            preview = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(preview.stdout)["address_reservations"], {})
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            page = vault / "wiki/sources/repo-project-knowledge.md"
            self.assertNotIn("\naddress:", page.read_text(encoding="utf-8"))

    def test_publish_preserves_legacy_shared_headers_without_closing_delimiter(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            for relative in ("wiki/index.md", "wiki/sources/_index.md", "wiki/log.md"):
                path = vault / relative
                source = path.read_text(encoding="utf-8")
                path.write_text(source.replace("\n---\n#", "\n#", 1), encoding="utf-8")
            payload = self._payload(repo, env)
            preview = subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--preview", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            self.assertFalse(json.loads(preview.stdout)["noop"])
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(payload), capture_output=True, text=True, check=True,
            )
            for relative in ("wiki/index.md", "wiki/sources/_index.md", "wiki/log.md"):
                source = (vault / relative).read_text(encoding="utf-8")
                self.assertTrue(source.startswith("---\n"))
                self.assertNotIn("\n---\n", source)

    def test_moved_knowledge_page_is_found_by_scope_id(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault, repo, env, _ = self._fixture(root)
            first = self._payload(repo, env)
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(first), capture_output=True, text=True, check=True,
            )
            original = vault / "wiki/sources/repo-project-knowledge.md"
            moved = vault / "wiki/meta/repo-project-knowledge.md"
            moved.parent.mkdir(parents=True)
            original.rename(moved)
            second = self._payload(repo, env, "Moved Knowledge")
            self.assertEqual(second["updates"][0]["page_hash"], __import__("hashlib").sha256(moved.read_bytes()).hexdigest())
            subprocess.run(
                ["python3", str(CLI), "--project-root", str(repo), "publish", "--apply", "--json"],
                env=env, input=json.dumps(second), capture_output=True, text=True, check=True,
            )
            self.assertTrue(moved.is_file())
            self.assertFalse(original.exists())
            self.assertIn(
                "[[wiki/meta/repo-project-knowledge|Moved Knowledge]]",
                (vault / "wiki/index.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
