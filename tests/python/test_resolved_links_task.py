import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ResolvedLinksTaskTests(unittest.TestCase):
    def test_daily_task_runs_after_catalog_sync_and_ignores_overlap(self):
        text = (ROOT / "scripts/install_resolved_links_task.ps1").read_text(encoding="utf-8")
        self.assertIn("12:30", text)
        self.assertIn("IgnoreNew", text)
        self.assertIn("run_resolved_links_sync.ps1", text)
        self.assertIn("Get-Command 'pwsh.exe'", text)
        self.assertIn("-Execute $pwshPath", text)

    def test_runner_updates_generated_manifest_and_sanitized_changelog(self):
        text = (ROOT / "scripts/run_resolved_links_sync.ps1").read_text(encoding="utf-8")
        self.assertIn("resolve_links.py --browser --background-window", text)
        self.assertNotIn("resolve_links.py --browser --headless", text)
        self.assertIn("public/data/resolved-links.json", text)
        self.assertIn("public/data/link-updates.json", text)
        self.assertIn("update_link_changelog.py --source resolved", text)
        self.assertIn("tests.python.test_resolved_links tests.python.test_link_updates", text)
        self.assertIn("Copy-Item", text)
        self.assertIn("HEAD:main", text)
        self.assertIn("$hasChanges = $LASTEXITCODE -eq 1", text)
        self.assertNotIn("--force", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("Bearer", text)


if __name__ == "__main__":
    unittest.main()
