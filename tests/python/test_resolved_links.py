import json
import tempfile
import unittest
from pathlib import Path

from src.giga_catalog.resolved_links import (
    atomic_write_json,
    build_manifest,
    iter_catalog_candidates,
    validate_final_url,
)


class ResolvedLinkCandidateTests(unittest.TestCase):
    def test_catalog_candidates_use_stable_standard_and_uncensored_slots(self):
        catalog = {
            "series": [{
                "code": "SPSF",
                "videos": [{
                    "code": "SPSF-58",
                    "releaseDate": "2026-09-11",
                    "links": {
                        "gofile": "https://ouo.io/normalGo",
                        "streamtape": "https://ouo.io/normalSt",
                        "uncensored": {
                            "gofile": "https://ouo.io/uncensoredGo",
                            "streamtape": "https://ouo.io/uncensoredSt",
                        },
                    },
                }],
            }],
        }
        candidates = list(iter_catalog_candidates(catalog))
        self.assertEqual(
            [(item.code, item.slot, item.provider) for item in candidates],
            [
                ("SPSF-58", "standard.streamtape", "streamtape"),
                ("SPSF-58", "standard.gofile", "gofile"),
                ("SPSF-58", "uncensored.streamtape", "streamtape"),
                ("SPSF-58", "uncensored.gofile", "gofile"),
            ],
        )
        self.assertTrue(all(item.source_url_hash.startswith("sha256:") for item in candidates))

    def test_final_url_validation_accepts_only_public_landing_pages(self):
        self.assertEqual(
            validate_final_url("https://gofile.io/d/N87ugOtd"),
            "https://gofile.io/d/N87ugOtd",
        )
        self.assertEqual(
            validate_final_url("https://streamtape.com/v/dKVZ8pvyRduk8vA/SPSF-58.mp4"),
            "https://streamtape.com/v/dKVZ8pvyRduk8vA/SPSF-58.mp4",
        )
        for value in (
            "http://gofile.io/d/N87ugOtd",
            "https://user:pass@gofile.io/d/N87ugOtd",
            "https://gofile.io/d/N87ugOtd#secret",
            "https://evil.example/d/N87ugOtd",
            "https://streamtape.com/get_video?id=file",
            "https://localhost/d/test",
        ):
            self.assertIsNone(validate_final_url(value), value)

    def test_manifest_preserves_matching_verified_entries_and_drops_stale_sources(self):
        catalog = {
            "series": [{"code": "SPSF", "videos": [{
                "code": "SPSF-58",
                "releaseDate": "2026-09-11",
                "links": {
                    "gofile": "https://ouo.io/normalGo",
                    "streamtape": "https://ouo.io/normalSt",
                },
            }]}],
        }
        candidates = list(iter_catalog_candidates(catalog))
        gofile = next(item for item in candidates if item.provider == "gofile")
        streamtape = next(item for item in candidates if item.provider == "streamtape")
        state = {
            "schemaVersion": 1,
            "results": {
                gofile.key: {
                    "sourceUrlHash": gofile.source_url_hash,
                    "status": "verified",
                    "finalUrl": "https://gofile.io/d/N87ugOtd",
                    "checkedAt": "2026-08-23T00:00:00Z",
                },
                streamtape.key: {
                    "sourceUrlHash": "sha256:" + "0" * 64,
                    "status": "verified",
                    "finalUrl": "https://streamtape.com/v/id/file.mp4",
                    "checkedAt": "2026-08-23T00:00:00Z",
                },
            },
        }
        manifest = build_manifest(candidates, state, generated_at="2026-08-23T01:00:00Z")
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(list(manifest["entries"]), ["SPSF-58"])
        self.assertEqual(list(manifest["entries"]["SPSF-58"]), ["standard.gofile"])
        self.assertEqual(
            manifest["entries"]["SPSF-58"]["standard.gofile"]["provider"],
            "gofile",
        )

    def test_atomic_json_writer_is_deterministic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "manifest.json"
            atomic_write_json(path, {"z": 1, "a": "中文"})
            first = path.read_bytes()
            atomic_write_json(path, {"a": "中文", "z": 1})
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(json.loads(first), {"a": "中文", "z": 1})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
