import copy
import unittest

from scripts.promote_javryo_catalog_proofs import promote_records
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION
from src.giga_catalog.resolved_links import source_url_hash


class CatalogProofPromotionTests(unittest.TestCase):
    def test_only_same_candidate_with_two_real_paths_and_trusted_catalog_click_is_promoted(self):
        url = "https://bysejikuar.com/e/example"
        inventory = {"EX-1": {"embedUrl": url, "sourceUrlHash": "source"}}
        original = {"results": {"EX-1": {"verificationVersion": VERIFICATION_VERSION,
            "sourceUrlHash": "source", "embedUrlHash": source_url_hash(url),
            "playbackStatus": "retryable", "paths": {"source": {"status": "retryable"}}}}}
        evidence = {"httpStatus": 200, "videoCount": 1, "manifestStatus": 200,
                    "events": ["playing"], "trustedVideoEvents": True, "playerDocumentValidated": True}
        proof = {"generation": "generation", "targetUrlHash": source_url_hash(url),
                 "verificationVersion": VERIFICATION_VERSION, "playbackStatus": "verified",
                 "checkedAt": "2026-10-01T00:00:00Z", "paths": {
                     "catalog": {"status": "verified", "evidence": {**evidence,
                         "catalogClickObserved": True, "catalogDocumentValidated": True}},
                     "direct": {"status": "verified", "evidence": evidence}}}
        promoted, codes = promote_records(original, {"EX-1": proof}, inventory, "generation")
        self.assertEqual(codes, ["EX-1"])
        self.assertEqual(promoted["results"]["EX-1"]["sourceKind"], "catalog")
        self.assertEqual(promoted["results"]["EX-1"]["finalUrl"], url)
        self.assertEqual(original["results"]["EX-1"]["playbackStatus"], "retryable")
        for key, value in (("generation", "old"), ("targetUrlHash", "old"),
                           ("verificationVersion", 1), ("playbackStatus", "retryable")):
            bad = copy.deepcopy(proof); bad[key] = value
            self.assertEqual(promote_records(original, {"EX-1": bad}, inventory, "generation")[1], [])
        bad = copy.deepcopy(proof)
        bad["paths"]["catalog"]["evidence"]["catalogClickObserved"] = False
        self.assertEqual(promote_records(original, {"EX-1": bad}, inventory, "generation")[1], [])
        bad = copy.deepcopy(proof)
        bad["paths"]["direct"]["evidence"]["events"] = []
        self.assertEqual(promote_records(original, {"EX-1": bad}, inventory, "generation")[1], [])
