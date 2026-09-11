import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.giga_catalog.link_updates import (
    diff_catalog_links,
    diff_resolved_links,
    merge_link_updates,
)


def catalog(*videos):
    return {"schemaVersion": 1, "series": [{"code": "SPSF", "videos": list(videos)}]}


class LinkUpdateTests(unittest.TestCase):
    def test_catalog_diff_reports_slots_without_exposing_urls(self):
        before = catalog(
            {"code": "SPSF-1", "links": {"reupload": "https://ouo.io/old"}},
            {"code": "SPSF-2", "links": {"gofile": "https://ouo.io/gone"}},
        )
        after = catalog(
            {
                "code": "SPSF-1",
                "links": {
                    "reupload": "https://ouo.io/new",
                    "uncensored": {"streamtape": "https://ouo.io/added"},
                },
            }
        )

        events = diff_catalog_links(before, after, at="2026-09-11T03:30:00Z")

        self.assertEqual(
            [(item["code"], item["slot"], item["action"]) for item in events],
            [
                ("SPSF-1", "standard.reupload", "updated"),
                ("SPSF-1", "uncensored.streamtape", "added"),
                ("SPSF-2", "standard.gofile", "removed"),
            ],
        )
        payload = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("https://", payload)
        self.assertNotIn("ouo.io", payload)

    def test_resolved_diff_records_only_new_verified_results(self):
        before = {
            "entries": {
                "SPSF-1": {
                    "standard.reupload": {
                        "status": "verified",
                        "kind": "external",
                        "provider": "streamtape",
                        "sourceUrlHash": "sha256:old",
                        "finalUrl": "https://streamtape.com/v/old",
                    }
                }
            }
        }
        after = {
            "entries": {
                "SPSF-1": {
                    "standard.reupload": {
                        "status": "verified",
                        "kind": "external",
                        "provider": "streamtape",
                        "sourceUrlHash": "sha256:new",
                        "finalUrl": "https://streamtape.com/v/new",
                    },
                    "standard.gofile": {"status": "pending"},
                }
            }
        }

        events = diff_resolved_links(before, after, at="2026-09-11T04:36:23Z")

        self.assertEqual(len(events), 1)
        self.assertEqual(
            {key: events[0][key] for key in ("source", "code", "slot", "action", "provider")},
            {
                "source": "resolved",
                "code": "SPSF-1",
                "slot": "standard.reupload",
                "action": "resolved",
                "provider": "streamtape",
            },
        )
        self.assertNotIn("url", json.dumps(events[0]).lower())

    def test_merge_deduplicates_and_keeps_only_thirty_days(self):
        fresh = {
            "id": "fresh",
            "at": "2026-09-10T03:30:00Z",
            "source": "catalog",
            "code": "SPSF-1",
            "slot": "standard.reupload",
            "action": "updated",
            "provider": "reupload",
        }
        stale = {**fresh, "id": "stale", "at": "2026-08-01T03:30:00Z"}
        history = {"schemaVersion": 1, "generatedAt": fresh["at"], "entries": [fresh, stale]}

        merged = merge_link_updates(
            history,
            [fresh],
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )

        self.assertEqual(merged["schemaVersion"], 1)
        self.assertEqual(merged["entries"], [fresh])
        self.assertEqual(merged["generatedAt"], fresh["at"])

    def test_merge_drops_malformed_history_that_could_leak_a_url(self):
        malicious = {
            "id": "bad",
            "at": "2026-09-10T03:30:00Z",
            "source": "catalog",
            "code": "https://private.example/secret",
            "slot": "standard.reupload",
            "action": "updated",
            "provider": "reupload",
        }

        merged = merge_link_updates(
            {"entries": [malicious]},
            [],
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )

        self.assertEqual(merged["entries"], [])

    def test_cli_merges_catalog_changes_into_a_sanitized_public_file(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            before = folder / "before.json"
            after = folder / "after.json"
            history = folder / "link-updates.json"
            before.write_text(json.dumps(catalog()), encoding="utf-8")
            changed = catalog({"code": "SPSF-58", "links": {"reupload": "https://ouo.io/secret"}})
            changed["generatedAt"] = "2026-09-11T03:30:00Z"
            after.write_text(json.dumps(changed), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/update_link_changelog.py"),
                    "--source", "catalog",
                    "--before", str(before),
                    "--after", str(after),
                    "--history", str(history),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = history.read_text(encoding="utf-8")
            self.assertNotIn("https://", payload)
            self.assertEqual(json.loads(payload)["entries"][0]["code"], "SPSF-58")


if __name__ == "__main__":
    unittest.main()
