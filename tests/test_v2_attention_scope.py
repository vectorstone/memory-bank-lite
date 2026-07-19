from datetime import date
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.mb_lite.attention import AttentionEntry, next_attention_id, parse_attention, render_attention
from scripts.mb_lite.bank import initialize_bank, validate_v2_bank
from scripts.mb_lite.bindings import BindingStore
from scripts.mb_lite.catalog import CatalogStore, generated_id, remote_project_id
from scripts.mb_lite.done import v2_bank_revision
from scripts.mb_lite.done import DonePayload, apply_done
from scripts.mb_lite.scope import resolve_scope
from scripts.mb_lite.start import build_v2_start_context
from tests.test_v2_config_catalog import V2ConfigCatalogTest


class V2AttentionScopeTest(unittest.TestCase):
    def test_attention_round_trip_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            bank = Path(value) / ".memory-bank"
            initialize_bank(bank)
            validate_v2_bank(bank)
            before = v2_bank_revision(bank)
            source = (bank / "attention.md").read_text(encoding="utf-8")
            entry = AttentionEntry(
                "ATT-001", "Preserve compatibility", "project", "high", "active",
                "Two clients share the contract", "ADR-001", date(2026, 7, 19), 90,
            )
            updated = render_attention(source, (entry,))
            (bank / "attention.md").write_text(updated, encoding="utf-8")
            self.assertEqual(parse_attention(updated), (entry,))
            self.assertEqual(next_attention_id((entry,)), "ATT-002")
            self.assertNotEqual(before, v2_bank_revision(bank))

    def test_done_adds_attention_and_start_surfaces_it(self) -> None:
        import json
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            bank = root / ".memory-bank"
            initialize_bank(bank)
            payload = DonePayload.from_json(json.dumps({
                "bank_revision": v2_bank_revision(bank),
                "summary": "Add compatibility guard",
                "decisions": [], "next_steps": [], "blockers": [], "key_files": [],
                "completed_items": [], "new_items": [], "note": None,
                "attention_changes": [{
                    "action": "add", "title": "Preserve compatibility", "scope": "project",
                    "severity": "high", "reason": "Shared clients", "evidence": "ADR-001",
                    "verified_at": "2026-07-19", "review_after_days": 90
                }]
            }))
            apply_done(bank, repo, payload, datetime.now(timezone.utc), revision_function=v2_bank_revision)
            context = build_v2_start_context(bank)
            self.assertIn("ATT-001 Preserve compatibility", context)

    def test_remote_project_scope_is_shared_by_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"], check=True)
            key = "github.com/example/repo"
            project_id = remote_project_id("repo", key)
            catalog = CatalogStore(vault)
            record = catalog.add_project(project_id, "repo", (key,))
            bank = vault / record.bank_path
            initialize_bank(bank)
            scope = resolve_scope(repo, catalog, BindingStore(root / "state"))
            self.assertEqual(scope.scope_id, project_id)
            self.assertEqual(scope.bank, bank.resolve())

    def test_no_remote_project_uses_explicit_local_binding(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            vault = V2ConfigCatalogTest()._vault(root)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            project_id = generated_id("repo")
            catalog = CatalogStore(vault)
            record = catalog.add_project(project_id, "repo", ())
            initialize_bank(vault / record.bank_path)
            bindings = BindingStore(root / "state")
            bindings.bind(repo, "project", project_id)
            self.assertEqual(resolve_scope(repo, catalog, bindings).scope_id, project_id)


if __name__ == "__main__":
    unittest.main()
