import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.giga_catalog.resolved_links import (
    atomic_write_json,
    build_manifest,
    iter_catalog_candidates,
    seed_state_from_manifest,
    validate_final_url,
)
from src.giga_catalog.resolved_links_browser import (
    choose_flow_url,
    collect_candidates,
    collect_candidates_parallel,
    is_human_verification_title,
    is_ouo_flow_url,
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
        self.assertEqual(
            validate_final_url("https://gigaandzen.embed4me.com/#a3nxx"),
            "https://gigaandzen.embed4me.com/#a3nxx",
        )
        self.assertIsNone(
            validate_final_url("https://gofile.io/d/N87ugOtd", expected_provider="streamtape")
        )
        for value in (
            "http://gofile.io/d/N87ugOtd",
            "https://user:pass@gofile.io/d/N87ugOtd",
            "https://gofile.io/d/N87ugOtd#secret",
            "https://evil.example/d/N87ugOtd",
            "https://streamtape.com/get_video?id=file",
            "https://localhost/d/test",
            "https://evil.embed4me.com/#a3nxx",
            "https://gigaandzen.embed4me.com/#bad-value",
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

    def test_manifest_keeps_allowlisted_destination_when_source_label_is_stale(self):
        catalog = {
            "series": [{"videos": [{
                "code": "ATHB-1",
                "links": {"streamtape": "https://ouo.io/NEMymt"},
            }]}],
        }
        candidate = next(iter(iter_catalog_candidates(catalog)))
        state = {"schemaVersion": 1, "results": {candidate.key: {
            "sourceUrlHash": candidate.source_url_hash,
            "status": "verified",
            "provider": "player4me",
            "finalUrl": "https://gigaandzen.embed4me.com/#nrf8u",
            "checkedAt": "2026-08-23T00:00:00Z",
            "attempts": 1,
        }}}
        manifest = build_manifest([candidate], state, generated_at="2026-08-23T01:00:00Z")
        entry = manifest["entries"]["ATHB-1"]["standard.streamtape"]
        self.assertEqual(entry["provider"], "player4me")
        self.assertEqual(entry["finalUrl"], "https://gigaandzen.embed4me.com/#nrf8u")

    def test_manifest_preserves_timestamp_when_public_entries_are_unchanged(self):
        catalog = {"series": [{"videos": [{
            "code": "SPSF-58",
            "links": {"gofile": "https://ouo.io/normalGo"},
        }]}]}
        candidate = next(iter(iter_catalog_candidates(catalog)))
        state = {"schemaVersion": 1, "results": {candidate.key: {
            "sourceUrlHash": candidate.source_url_hash,
            "status": "verified",
            "provider": "gofile",
            "finalUrl": "https://gofile.io/d/N87ugOtd",
            "checkedAt": "2026-08-23T00:00:00Z",
        }}}
        previous = build_manifest([candidate], state, generated_at="2026-08-23T01:00:00Z")
        current = build_manifest(
            [candidate],
            state,
            generated_at="2026-08-24T01:00:00Z",
            previous_manifest=previous,
        )
        self.assertEqual(current, previous)

    def test_existing_public_manifest_seeds_empty_private_state(self):
        catalog = {
            "series": [{"videos": [{
                "code": "SPSF-58",
                "links": {"gofile": "https://ouo.io/normalGo"},
            }]}],
        }
        candidate = next(iter(iter_catalog_candidates(catalog)))
        manifest = {
            "schemaVersion": 2,
            "entries": {"SPSF-58": {"standard.gofile": {
                "provider": "gofile",
                "sourceUrlHash": candidate.source_url_hash,
                "finalUrl": "https://gofile.io/d/N87ugOtd",
                "kind": "external",
                "status": "verified",
                "checkedAt": "2026-08-23T00:00:00Z",
            }}},
        }
        state = seed_state_from_manifest([candidate], manifest, {"schemaVersion": 1, "results": {}})
        self.assertEqual(state["results"][candidate.key]["status"], "verified")

    def test_atomic_json_writer_is_deterministic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "manifest.json"
            atomic_write_json(path, {"z": 1, "a": "中文"})
            first = path.read_bytes()
            atomic_write_json(path, {"a": "中文", "z": 1})
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(json.loads(first), {"a": "中文", "z": 1})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class ResolvedLinkCollectorTests(unittest.IsolatedAsyncioTestCase):
    def test_localized_cloudflare_title_is_human_verification(self):
        self.assertTrue(is_human_verification_title("请稍候…"))
        self.assertTrue(is_human_verification_title("Just a moment..."))
        self.assertFalse(is_human_verification_title("OUO link flow"))

    def test_ouo_flow_accepts_the_service_canonical_press_host_only(self):
        self.assertTrue(is_ouo_flow_url("https://ouo.io/abc123"))
        self.assertTrue(is_ouo_flow_url("https://ouo.press/go/abc123"))
        self.assertFalse(is_ouo_flow_url("https://evil.example/abc123"))

    def test_popup_selection_prefers_final_then_ouo_over_advertising(self):
        urls = [
            "https://ads.example/landing",
            "https://ouo.press/go/abc123",
            "https://streamtape.com/v/id/file.mp4",
        ]
        self.assertEqual(choose_flow_url(urls), urls[2])
        self.assertEqual(choose_flow_url(urls[:2]), urls[1])

    async def test_collector_checkpoints_verified_and_failed_results_and_resumes(self):
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
        resolver = AsyncMock(side_effect=[
            {"status": "verified", "finalUrl": "https://streamtape.com/v/id/SPSF-58.mp4"},
            {"status": "blocked-human", "errorCode": "human-verification"},
        ])
        checkpoints = []
        state = {"schemaVersion": 1, "results": {}}
        processed = await collect_candidates(
            candidates,
            state,
            resolver,
            checkpoint=lambda value: checkpoints.append(json.loads(json.dumps(value))),
        )
        self.assertEqual(processed, 2)
        self.assertEqual(len(checkpoints), 2)
        first = state["results"][candidates[0].key]
        self.assertEqual(first["status"], "verified")
        self.assertEqual(first["sourceUrlHash"], candidates[0].source_url_hash)
        second = state["results"][candidates[1].key]
        self.assertEqual(second["status"], "blocked-human")
        self.assertNotIn("finalUrl", second)

        resolver.reset_mock()
        resumed = await collect_candidates(
            candidates,
            state,
            resolver,
            checkpoint=lambda value: None,
        )
        self.assertEqual(resumed, 0)
        resolver.assert_not_awaited()

    async def test_collector_never_marks_unknown_destination_verified(self):
        candidate = next(iter(iter_catalog_candidates({
            "series": [{"videos": [{
                "code": "SPSF-58",
                "links": {"gofile": "https://ouo.io/normalGo"},
            }]}],
        })))
        resolver = AsyncMock(return_value={
            "status": "verified",
            "finalUrl": "https://evil.example/watch",
            "observedHost": "evil.example",
        })
        state = {"schemaVersion": 1, "results": {}}
        await collect_candidates([candidate], state, resolver, checkpoint=lambda value: None)
        result = state["results"][candidate.key]
        self.assertEqual(result["status"], "retryable")
        self.assertNotIn("finalUrl", result)
        self.assertEqual(result["observedHost"], "evil.example")

    async def test_retryable_result_is_processed_again_on_resume(self):
        candidate = next(iter(iter_catalog_candidates({
            "series": [{"videos": [{
                "code": "SPSF-58",
                "links": {"gofile": "https://ouo.io/normalGo"},
            }]}],
        })))
        state = {"schemaVersion": 1, "results": {candidate.key: {
            "sourceUrlHash": candidate.source_url_hash,
            "status": "retryable",
            "attempts": 1,
        }}}
        resolver = AsyncMock(return_value={
            "status": "verified",
            "finalUrl": "https://gofile.io/d/N87ugOtd",
        })
        processed = await collect_candidates([candidate], state, resolver, checkpoint=lambda value: None)
        self.assertEqual(processed, 1)
        self.assertEqual(state["results"][candidate.key]["status"], "verified")
        self.assertEqual(state["results"][candidate.key]["attempts"], 2)

    async def test_persistent_unknown_destination_becomes_unsupported(self):
        candidate = next(iter(iter_catalog_candidates({
            "series": [{"videos": [{
                "code": "OLD-1",
                "links": {"streamtape": "https://ouo.io/oldLink"},
            }]}],
        })))
        state = {"schemaVersion": 1, "results": {candidate.key: {
            "sourceUrlHash": candidate.source_url_hash,
            "status": "retryable",
            "attempts": 3,
        }}}
        resolver = AsyncMock(return_value={
            "status": "retryable",
            "errorCode": "unknown-destination",
            "observedHost": "strmup.to",
        })
        await collect_candidates([candidate], state, resolver, checkpoint=lambda value: None)
        result = state["results"][candidate.key]
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["attempts"], 4)
        self.assertEqual(result["observedHost"], "strmup.to")

    async def test_parallel_collector_processes_each_candidate_once(self):
        catalog = {"series": [{"videos": [
            {"code": f"SPSF-{number}", "links": {"gofile": f"https://ouo.io/code{number}"}}
            for number in range(1, 5)
        ]}]}
        candidates = list(iter_catalog_candidates(catalog))

        async def resolve(candidate):
            return {"status": "verified", "finalUrl": f"https://gofile.io/d/{candidate.code.replace('-', '')}"}

        state = {"schemaVersion": 1, "results": {}}
        checkpoints = []
        processed = await collect_candidates_parallel(
            candidates,
            state,
            [resolve, resolve],
            checkpoint=lambda value: checkpoints.append(len(value["results"])),
        )
        self.assertEqual(processed, 4)
        self.assertEqual(len(state["results"]), 4)
        self.assertEqual(checkpoints, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
