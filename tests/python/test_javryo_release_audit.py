import copy
import unittest

from scripts import audit_javryo_release as audit
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION
from src.giga_catalog.resolved_links import source_url_hash


class CatalogPreviewReleaseGateTests(unittest.TestCase):
    def test_preview_cannot_publish_stale_target_generation_or_weaker_playback(self):
        entry = {"finalUrl": "https://bysejikuar.com/e/example"}
        evidence = {"httpStatus": 200, "videoCount": 1, "manifestStatus": 200,
                    "trustedVideoEvents": True, "playerDocumentValidated": True,
                    "events": ["playing"]}
        row = {"generation": "generation", "targetUrlHash": source_url_hash(entry["finalUrl"]),
               "verificationVersion": VERIFICATION_VERSION, "playbackStatus": "verified",
               "paths": {name: {"status": "verified", "evidence": copy.deepcopy(evidence)}
                         for name in ("catalog", "direct")}}
        audit.assert_preview_entry("EX-1", entry, row, "generation")
        for key, value in (("generation", "older"), ("targetUrlHash", "stale"),
                           ("verificationVersion", 1), ("playbackStatus", "retryable")):
            bad = copy.deepcopy(row)
            bad[key] = value
            with self.assertRaises(AssertionError):
                audit.assert_preview_entry("EX-1", entry, bad, "generation")
        bad = copy.deepcopy(row)
        bad["paths"]["direct"]["evidence"]["events"] = []
        with self.assertRaises(AssertionError):
            audit.assert_preview_entry("EX-1", entry, bad, "generation")
