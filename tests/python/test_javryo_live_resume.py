import copy
import unittest
from datetime import datetime, timezone

from scripts.verify_javryo_live import reusable_path
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION
from src.giga_catalog.resolved_links import source_url_hash


class LivePathResumeTests(unittest.TestCase):
    def test_only_recent_matching_verified_paths_can_resume(self):
        url = "https://bysejikuar.com/e/example"
        now = datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc)
        evidence = {"httpStatus": 200, "videoCount": 1, "manifestStatus": 200,
                    "events": ["playing"], "trustedVideoEvents": True,
                    "playerDocumentValidated": True, "catalogClickObserved": True,
                    "catalogDocumentValidated": True}
        row = {"generation": "generation", "targetUrlHash": source_url_hash(url),
               "verificationVersion": VERIFICATION_VERSION, "checkedAt": "2026-10-01T00:00:00Z",
               "paths": {"catalog": {"status": "verified", "evidence": evidence},
                         "direct": {"status": "retryable", "evidence": {}}}}
        self.assertTrue(reusable_path(row, "catalog", url, "generation", now=now))
        self.assertFalse(reusable_path(row, "direct", url, "generation", now=now))
        for key, value in (("generation", "old"), ("targetUrlHash", "old"),
                           ("verificationVersion", 1), ("checkedAt", "2026-09-30T00:00:00Z")):
            bad = copy.deepcopy(row);bad[key] = value
            self.assertFalse(reusable_path(bad, "catalog", url, "generation", now=now))
        bad = copy.deepcopy(row);bad["paths"]["catalog"]["evidence"]["catalogClickObserved"] = False
        self.assertFalse(reusable_path(bad, "catalog", url, "generation", now=now))
