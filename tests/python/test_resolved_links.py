import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.giga_catalog import resolved_links_browser
from src.giga_catalog.resolved_links import (
    atomic_write_json,
    build_manifest,
    filter_candidates_by_codes,
    iter_catalog_candidates,
    seed_state_from_manifest,
    validate_final_url,
)
from src.giga_catalog.resolved_links_browser import (
    choose_flow_url,
    click_flow_button,
    collect_candidates,
    collect_candidates_parallel,
    is_human_verification_title,
    is_ouo_flow_url,
)


class ResolvedLinkCandidateTests(unittest.TestCase):
    def test_code_filter_limits_browser_work_without_dropping_manifest_candidates(self):
        catalog = {
            "series": [{"videos": [
                {"code": "SPSF-64", "links": {"gofile": "https://ouo.io/spsf64"}},
                {"code": "TBB-77", "links": {"gofile": "https://ouo.io/tbb77"}},
                {"code": "TRE-41", "links": {"gofile": "https://ouo.io/tre41"}},
            ]}],
        }
        candidates = list(iter_catalog_candidates(catalog))

        filtered = filter_candidates_by_codes(
            candidates,
            [" spsf-64 ", "TBB-77", "spsf-64"],
        )

        self.assertEqual([item.code for item in filtered], ["SPSF-64", "TBB-77"])
        self.assertEqual([item.code for item in candidates], ["SPSF-64", "TBB-77", "TRE-41"])

    def test_catalog_candidates_use_stable_standard_and_uncensored_slots(self):
        catalog = {
            "series": [{
                "code": "SPSF",
                "videos": [{
                    "code": "SPSF-58",
                    "releaseDate": "2026-09-11",
                    "links": {
                        "reupload": "https://ouo.io/latestUpload",
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
                ("SPSF-58", "standard.reupload", "reupload"),
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

    def test_final_url_validation_accepts_vidara_watch_pages(self):
        self.assertEqual(
            validate_final_url("https://vidara.to/e/GSHPFUIm9UPKy"),
            "https://vidara.to/e/GSHPFUIm9UPKy",
        )
        self.assertEqual(
            validate_final_url("https://vidara.so/v/6uTHDGn6r8BA4"),
            "https://vidara.so/v/6uTHDGn6r8BA4",
        )

    def test_final_url_validation_rejects_parked_or_malformed_new_hosts(self):
        for value in (
            "https://strmup.cc/",
            "https://ww19.strmup.to/",
            "https://strmup.to/",
            "https://strmup.to/get/t/file-id",
            "https://strmup.to/edm0O2yFbplzH?ch=1&js=temporary&sid=session",
            "https://vidara.to/",
            "https://vidara.to/download/GSHPFUIm9UPKy",
            "https://vidara.to:444/e/GSHPFUIm9UPKy",
            "https://evil.vidara.to/e/GSHPFUIm9UPKy",
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

    def test_manifest_reuses_a_verified_destination_for_identical_source_urls(self):
        catalog = {
            "series": [{"videos": [{
                "code": "THZA-10",
                "links": {
                    "reupload": "https://ouo.io/sameSource",
                    "streamtape": "https://ouo.io/sameSource",
                },
            }]}],
        }
        candidates = list(iter_catalog_candidates(catalog))
        verified = candidates[0]
        state = {"schemaVersion": 1, "results": {verified.key: {
            "sourceUrlHash": verified.source_url_hash,
            "status": "verified",
            "provider": "streamtape",
            "finalUrl": "https://streamtape.com/v/id/THZA-10.mp4",
            "checkedAt": "2026-09-20T00:00:00Z",
        }}}

        manifest = build_manifest(candidates, state, generated_at="2026-09-20T01:00:00Z")

        entries = manifest["entries"]["THZA-10"]
        self.assertEqual(
            entries["standard.reupload"]["finalUrl"],
            "https://streamtape.com/v/id/THZA-10.mp4",
        )
        self.assertEqual(
            entries["standard.streamtape"]["finalUrl"],
            "https://streamtape.com/v/id/THZA-10.mp4",
        )

    def test_manifest_does_not_reuse_a_destination_across_different_codes(self):
        catalog = {
            "series": [{"videos": [
                {"code": "SPSF-64", "links": {"gofile": "https://ouo.io/shared"}},
                {"code": "SPSF-65", "links": {"gofile": "https://ouo.io/shared"}},
            ]}],
        }
        candidates = list(iter_catalog_candidates(catalog))
        verified = candidates[0]
        state = {"schemaVersion": 1, "results": {verified.key: {
            "sourceUrlHash": verified.source_url_hash,
            "status": "verified",
            "provider": "gofile",
            "finalUrl": "https://gofile.io/d/onlySPSF64",
            "checkedAt": "2026-09-20T00:00:00Z",
        }}}

        manifest = build_manifest(candidates, state, generated_at="2026-09-20T01:00:00Z")

        self.assertIn("SPSF-64", manifest["entries"])
        self.assertNotIn("SPSF-65", manifest["entries"])

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
    async def test_flow_button_click_bypasses_an_ad_overlay(self):
        class OverlayProtectedButton:
            def __init__(self):
                self.first = self
                self.clicked = False

            async def click(self, *, force=False, **_kwargs):
                if not force:
                    raise RuntimeError("ad overlay intercepted pointer events")
                self.clicked = True

        button = OverlayProtectedButton()

        self.assertTrue(await click_flow_button(button))
        self.assertTrue(button.clicked)

    def test_background_browser_uses_headed_chrome_offscreen_without_focus(self):
        build_options = getattr(resolved_links_browser, "build_browser_launch_options", None)
        self.assertIsNotNone(build_options)
        options = build_options(headless=False, background_window=True)
        self.assertFalse(options["headless"])
        self.assertEqual(
            options["args"],
            ["--start-minimized", "--window-position=-32000,-32000"],
        )

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

    async def test_collector_checkpoints_verified_and_retries_human_verification_on_resume(self):
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

        resolver.reset_mock(return_value=True, side_effect=True)
        resolver.return_value = {
            "status": "verified",
            "finalUrl": "https://gofile.io/d/recoveredAfterCaptcha",
        }
        resumed = await collect_candidates(
            candidates,
            state,
            resolver,
            checkpoint=lambda value: None,
        )
        self.assertEqual(resumed, 1)
        resolver.assert_awaited_once_with(candidates[1])
        self.assertEqual(state["results"][candidates[1].key]["status"], "verified")

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
