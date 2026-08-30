import copy
import json
import random
import unittest
from pathlib import Path

from scripts.migrate_legacy import migrate_legacy
from src.giga_catalog.merge import build_catalog, serialize_catalog


GENERATED_AT = "2026-07-29T00:00:00Z"
LEGACY_DIR = Path(r"D:\giga-catalog")


def product(code, date="2026-07-01", **overrides):
    value = {
        "code": code,
        "title": f"Title {code}",
        "actors": ["Actor"],
        "releaseDate": date,
        "cover": "https://example.test/cover.jpg",
    }
    value.update(overrides)
    return value


class DeterministicMergeTests(unittest.TestCase):
    def test_preserves_vidara_links_from_the_live_sheet(self) -> None:
        catalog, _ = build_catalog(
            [product("SPSF-61")],
            {"SPSF-61": {"vidara": "https://ouo.io/vidara"}},
            generated_at=GENERATED_AT,
        )

        self.assertEqual(
            catalog["series"][0]["videos"][0]["links"],
            {"vidara": "https://ouo.io/vidara"},
        )

    def test_publishes_normalized_tag_index_and_video_tag_references(self) -> None:
        catalog, _ = build_catalog(
            [
                product(
                    "SPSF-1",
                    tagIds=[25, 6],
                    tagsStatus="complete",
                    tagsUpdatedAt=GENERATED_AT,
                    tagsSource="official",
                )
            ],
            {},
            generated_at=GENERATED_AT,
            tags=[
                {"id": 6, "group": "genre", "nameJa": "陰落", "nameZh": "沦陷"},
                {"id": 25, "group": "genre", "nameJa": "黒髪", "nameZh": "黑发"},
            ],
        )

        video = catalog["series"][0]["videos"][0]
        self.assertEqual(video["tagIds"], [6, 25])
        self.assertEqual(video["tagsStatus"], "complete")
        self.assertEqual(video["tagsUpdatedAt"], GENERATED_AT)
        self.assertEqual(video["tagsSource"], "official")
        self.assertEqual([tag["count"] for tag in catalog["tags"]], [1, 1])

    def test_adapts_legacy_records_and_derives_code_fields(self) -> None:
        """Trusting legacy field names or supplied series data would leak a second schema."""
        catalog, summary = build_catalog(
            [
                {
                    "code": "spsf_0044",
                    "title": "Legacy title",
                    "actors": ["Actor"],
                    "date": "2026-07-01",
                    "cover": "https://example.test/cover.jpg",
                    "productId": None,
                }
            ],
            {},
            generated_at=GENERATED_AT,
        )

        video = catalog["series"][0]["videos"][0]
        self.assertEqual(video["code"], "SPSF-44")
        self.assertEqual(video["number"], 44)
        self.assertEqual(video["releaseDate"], "2026-07-01")
        self.assertNotIn("date", video)
        self.assertNotIn("series", video)
        self.assertNotIn("productId", video)
        self.assertEqual(
            catalog["series"][0],
            {
                "code": "SPSF",
                "count": 1,
                "firstReleaseDate": "2026-07-01",
                "latestReleaseDate": "2026-07-01",
                "videos": [video],
            },
        )
        self.assertEqual(summary["counts"]["acceptedProducts"], 1)

    def test_backfills_compact_previews_from_canonical_giga_covers(self) -> None:
        """Legacy records regain the proven 18-image gallery without extra crawling."""
        catalog, _ = build_catalog(
            [
                product(
                    "PMID-92",
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles//pmid/pmid092/pac_s.jpg"
                    ),
                    productId=123,
                ),
                product(
                    "SPSF-1",
                    cover="https://example.test/db_titles/spsf/x/pac_s.jpg",
                ),
                product(
                    "SPSF-2",
                    cover=(
                        "https://www.giga-web.jp/db_titles/spsf/custom/pac_s.jpg"
                    ),
                    productId=124,
                    previewBase=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/custom/sample/"
                    ),
                    previewCount=31,
                ),
                product(
                    "SPSF-3",
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/unverified/pac_s.jpg"
                    ),
                ),
            ],
            {},
            generated_at=GENERATED_AT,
        )

        videos = {
            video["code"]: video
            for series in catalog["series"]
            for video in series["videos"]
        }
        self.assertEqual(
            (
                videos["PMID-92"]["previewBase"],
                videos["PMID-92"]["previewCount"],
            ),
            (
                "https://www.giga-web.jp/db_titles/pmid/pmid092/sample/",
                18,
            ),
        )
        self.assertNotIn("previewBase", videos["SPSF-1"])
        self.assertNotIn("previewCount", videos["SPSF-1"])
        self.assertEqual(
            (
                videos["SPSF-2"]["previewBase"],
                videos["SPSF-2"]["previewCount"],
            ),
            (
                "https://www.giga-web.jp/"
                "db_titles/spsf/custom/sample/",
                31,
            ),
        )
        self.assertNotIn("previewBase", videos["SPSF-3"])
        self.assertNotIn("previewCount", videos["SPSF-3"])

    def test_preview_backfill_is_counted_as_a_metadata_update(self) -> None:
        """Canonicalizing the old snapshot must not hide a published schema change."""
        previous = build_catalog(
            [
                product(
                    "SPSF-1",
                    productId=123,
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/spsf0001/pac_s.jpg"
                    ),
                )
            ],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )[0]
        old_video = previous["series"][0]["videos"][0]
        old_video.pop("previewBase")
        old_video.pop("previewCount")

        catalog, summary = build_catalog(
            [],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "incremental", "scanComplete": False},
        )

        video = catalog["series"][0]["videos"][0]
        self.assertEqual(video["previewCount"], 18)
        self.assertEqual(catalog["refresh"]["counts"]["updated"], 1)
        self.assertEqual(catalog["refresh"]["counts"]["retained"], 0)
        self.assertEqual(summary["codes"]["updated"], ["SPSF-1"])

    def test_links_only_does_not_backfill_preview_metadata(self) -> None:
        """A link refresh cannot silently migrate old product metadata."""
        previous = build_catalog(
            [
                product(
                    "SPSF-1",
                    productId=123,
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/spsf0001/pac_s.jpg"
                    ),
                )
            ],
            {"SPSF-1": {"gofile": "https://example.test/old"}},
            generated_at="2026-07-28T00:00:00Z",
        )[0]
        old_video = previous["series"][0]["videos"][0]
        old_video.pop("previewBase")
        old_video.pop("previewCount")

        catalog, summary = build_catalog(
            [],
            {"SPSF-1": {"gofile": "https://example.test/new"}},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "links-only", "scanComplete": False},
        )

        video = catalog["series"][0]["videos"][0]
        self.assertNotIn("previewBase", video)
        self.assertNotIn("previewCount", video)
        self.assertEqual(summary["counts"]["updated"], 0)
        self.assertEqual(summary["counts"]["retained"], 1)
        self.assertEqual(
            video["links"],
            {"gofile": "https://example.test/new"},
        )

    def test_known_exact_preview_count_survives_directory_rediscovery(self) -> None:
        """A weekly directory audit must not downgrade a detail-page count to 18."""
        previous = build_catalog(
            [
                product(
                    "SPSF-1",
                    productId=123,
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/current/pac_s.jpg"
                    ),
                    previewBase=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/current/sample/"
                    ),
                    previewCount=31,
                )
            ],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )[0]

        catalog, _ = build_catalog(
            [
                product(
                    "SPSF-1",
                    productId=123,
                    cover=(
                        "https://www.giga-web.jp/"
                        "db_titles/spsf/current/pac_s.jpg"
                    ),
                )
            ],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        video = catalog["series"][0]["videos"][0]
        self.assertEqual(video["previewCount"], 31)

    def test_known_verified_tags_survive_directory_rediscovery(self) -> None:
        """Search cards cannot erase tag evidence that only product details provide."""
        previous, _ = build_catalog(
            [
                product(
                    "SPSF-1",
                    title="Old title",
                    productId=101,
                    tagIds=[6, 25],
                    tagsStatus="complete",
                    tagsUpdatedAt="2026-08-20T00:00:00Z",
                    tagsSource="official",
                )
            ],
            {},
            generated_at="2026-08-20T00:00:00Z",
        )

        current, _ = build_catalog(
            [product("SPSF-1", title="Updated title", productId=101)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        video = current["series"][0]["videos"][0]
        self.assertEqual(video["title"], "Updated title")
        self.assertEqual(video["tagIds"], [6, 25])
        self.assertEqual(video["tagsStatus"], "complete")
        self.assertEqual(video["tagsUpdatedAt"], "2026-08-20T00:00:00Z")
        self.assertEqual(video["tagsSource"], "official")

    def test_duplicate_winner_is_quality_ranked_and_permutation_independent(self) -> None:
        """Input order must not decide which duplicate metadata is published."""
        candidates = [
            product(
                "SPSF-44",
                date="not-a-date",
                title="涓涓涓",
                actors=[],
                cover=None,
                productId=None,
            ),
            product("SPSF-44", title="Clean title", productId=8123),
            product("SPSF-44", title="Another clean title", productId=8123),
        ]

        first, first_summary = build_catalog(
            candidates,
            {},
            generated_at=GENERATED_AT,
        )
        second, second_summary = build_catalog(
            list(reversed(candidates)),
            {},
            generated_at=GENERATED_AT,
        )

        self.assertEqual(serialize_catalog(first), serialize_catalog(second))
        self.assertEqual(
            json.dumps(first_summary, ensure_ascii=False, sort_keys=True),
            json.dumps(second_summary, ensure_ascii=False, sort_keys=True),
        )
        selected = first["series"][0]["videos"][0]
        self.assertEqual(selected["productId"], 8123)
        self.assertIn(selected["title"], {"Clean title", "Another clean title"})
        self.assertEqual(first_summary["counts"]["discardedDuplicates"], 2)

    def test_duplicate_quality_rejects_all_documented_mojibake_markers(self) -> None:
        """Observed and plan-documented corruption markers must lose to clean text."""
        for corrupted_title in ("丐丐 broken", "乓乓 broken", "涓涓 broken", "涔涔 broken"):
            with self.subTest(corrupted_title=corrupted_title):
                catalog, _ = build_catalog(
                    [
                        product("SPSF-1", title=corrupted_title, productId=1),
                        product("SPSF-1", title="Clean title", productId=1),
                    ],
                    {},
                    generated_at=GENERATED_AT,
                )
                self.assertEqual(
                    catalog["series"][0]["videos"][0]["title"],
                    "Clean title",
                )

    def test_strips_empty_link_leaves_and_serializes_deterministically(self) -> None:
        """Blank legacy providers and mapping insertion order must not affect public bytes."""
        products = [product("SPSF-2"), product("SPSF-1")]
        links_a = {
            "spsf_0002": {
                "gofile": "",
                "streamtape": "https://example.test/st-2",
                "uncensored": {"gofile": " ", "player4me": ""},
            },
            "SPSF-001": {
                "gofile": "https://example.test/gf-1",
                "streamtape": "",
            },
        }
        links_b = {
            "SPSF-001": dict(reversed(list(links_a["SPSF-001"].items()))),
            "spsf_0002": dict(reversed(list(links_a["spsf_0002"].items()))),
        }

        first, _ = build_catalog(products, links_a, generated_at=GENERATED_AT)
        second, _ = build_catalog(
            list(reversed(products)), links_b, generated_at=GENERATED_AT
        )

        self.assertEqual(serialize_catalog(first), serialize_catalog(second))
        self.assertTrue(serialize_catalog(first).endswith(b"\n"))
        self.assertNotIn(b": ", serialize_catalog(first))
        videos = first["series"][0]["videos"]
        self.assertEqual(videos[0]["links"], {"gofile": "https://example.test/gf-1"})
        self.assertEqual(
            videos[1]["links"],
            {"streamtape": "https://example.test/st-2"},
        )
        self.assertNotIn("uncensored", videos[1]["links"])

    def test_publishes_video_series_and_catalog_subtitle_scopes(self) -> None:
        """Collapsing the global portal or a series folder onto every video breaks scope."""
        catalog, _ = build_catalog(
            [product("SPSF-44")],
            {
                "spsf_044": {
                    "subtitle": "https://drive.google.com/file/d/spsf44/view"
                }
            },
            generated_at=GENERATED_AT,
            series_links={
                "spsf": {
                    "subtitle": "https://drive.google.com/drive/folders/spsf"
                }
            },
            resources={
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            },
        )

        series = catalog["series"][0]
        self.assertEqual(
            catalog["resources"],
            {
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            },
        )
        self.assertEqual(
            series["links"],
            {"subtitle": "https://drive.google.com/drive/folders/spsf"},
        )
        self.assertEqual(
            series["videos"][0]["links"],
            {"subtitle": "https://drive.google.com/file/d/spsf44/view"},
        )

    def test_preserves_verified_subtitles_when_a_later_overlay_omits_them(self) -> None:
        """A blank later source observation is not an append-only subtitle tombstone."""
        previous, _ = build_catalog(
            [product("SPSF-44")],
            {
                "SPSF-44": {
                    "subtitle": "https://drive.google.com/file/d/spsf44/view"
                }
            },
            generated_at="2026-07-28T00:00:00Z",
            series_links={
                "SPSF": {
                    "subtitle": "https://drive.google.com/drive/folders/spsf"
                }
            },
            resources={
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            },
        )

        current, _ = build_catalog(
            [product("SPSF-44")],
            {"SPSF-44": {"gofile": "https://links.example/spsf44"}},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            resources={},
            series_links={},
        )

        series = current["series"][0]
        self.assertEqual(current["resources"], previous["resources"])
        self.assertEqual(series["links"], previous["series"][0]["links"])
        self.assertEqual(
            series["videos"][0]["links"],
            {
                "gofile": "https://links.example/spsf44",
                "subtitle": "https://drive.google.com/file/d/spsf44/view",
            },
        )

    def test_verified_subtitles_win_conflicts_while_explicit_portal_updates(self) -> None:
        """Append-only applies to resolved links, while the explicit portal remains current."""
        previous, _ = build_catalog(
            [product("SPSF-44")],
            {"SPSF-44": {"subtitle": "https://z.example/video"}},
            generated_at="2026-07-28T00:00:00Z",
            series_links={
                "SPSF": {
                    "subtitle": "https://drive.google.com/open?id=zprevious"
                }
            },
            resources={
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/zprevious",
                }
            },
        )

        current, summary = build_catalog(
            [product("SPSF-44")],
            {"SPSF-44": {"subtitle": "https://a.example/video"}},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            series_links={
                "SPSF": {
                    "subtitle": "https://drive.google.com/open?id=aincoming"
                }
            },
            resources={
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/aincoming",
                }
            },
        )

        series = current["series"][0]
        self.assertEqual(
            series["videos"][0]["links"]["subtitle"],
            "https://z.example/video",
        )
        self.assertEqual(
            series["links"]["subtitle"],
            "https://drive.google.com/open?id=zprevious",
        )
        self.assertEqual(
            current["resources"]["subtitleDirectory"]["url"],
            "https://ouo.io/aincoming",
        )
        self.assertEqual(current["refresh"]["counts"]["linkConflicts"], 2)
        self.assertEqual(summary["counts"]["diagnostics"], 2)

    def test_sorts_series_by_latest_date_and_videos_by_number(self) -> None:
        """Source traversal order must not become presentation order."""
        catalog, _ = build_catalog(
            [
                product("OLD-9", date="2024-01-01"),
                product("NEW-10", date="2026-01-02"),
                product("NEW-2", date="2026-01-01"),
            ],
            {},
            generated_at=GENERATED_AT,
        )

        self.assertEqual([series["code"] for series in catalog["series"]], ["NEW", "OLD"])
        self.assertEqual(
            [video["number"] for video in catalog["series"][0]["videos"]],
            [2, 10],
        )

    def test_accounts_for_metadata_and_link_diffs_separately(self) -> None:
        """A link-only change must not inflate the product metadata update count."""
        previous, _ = build_catalog(
            [
                product("SPSF-1", productId=1),
                product("SPSF-2", productId=2),
                product("SPSF-3", productId=3),
            ],
            {
                "SPSF-1": {"gofile": "https://example.test/old"},
                "SPSF-2": {"gofile": "https://example.test/remove"},
            },
            generated_at="2026-07-28T00:00:00Z",
        )
        current, summary = build_catalog(
            [
                product("SPSF-1", productId=1),
                product("SPSF-2", title="Updated title", productId=2),
                product("SPSF-4", productId=4),
            ],
            {
                "SPSF-1": {"gofile": "https://example.test/new"},
                "SPSF-4": {"streamtape": "https://example.test/add"},
            },
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        self.assertEqual(
            current["refresh"]["counts"],
            {
                "added": 1,
                "updated": 1,
                "retained": 1,
                "deleted": 1,
                "linked": 2,
                "linkAdded": 1,
                "linkUpdated": 1,
                "linkRemoved": 1,
                "linkConflicts": 0,
            },
        )
        self.assertEqual(summary["codes"]["added"], ["SPSF-4"])
        self.assertEqual(summary["codes"]["updated"], ["SPSF-2"])
        self.assertEqual(summary["codes"]["deleted"], ["SPSF-3"])
        self.assertEqual(summary["codes"]["linkUpdated"], ["SPSF-1"])
        self.assertNotIn("diagnostics", current["refresh"])

    def test_incremental_updates_incoming_codes_and_retains_absent_previous_codes(self) -> None:
        """A deterministic tie-break must not block a newer official metadata update."""
        previous, _ = build_catalog(
            [
                product("SPSF-1", title="Zulu old title", productId=1),
                product("SPSF-2", title="Retained", productId=2),
            ],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )

        current, _ = build_catalog(
            [product("SPSF-1", title="Alpha new title", productId=1)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "incremental", "scanComplete": False},
        )

        videos = {
            video["code"]: video
            for series in current["series"]
            for video in series["videos"]
        }
        self.assertEqual(videos["SPSF-1"]["title"], "Alpha new title")
        self.assertEqual(videos["SPSF-2"]["title"], "Retained")
        self.assertEqual(current["refresh"]["counts"]["deleted"], 0)

    def test_unbounded_authoritative_audit_retains_legacy_archive_records(self) -> None:
        """A complete official scan cannot erase explicit archive-only history."""
        previous, _ = build_catalog(
            [
                product("ARCH-2", title="Second archive"),
                product("LIVE-1", productId=101),
                product("ARCH-1", title="First archive", productId=None),
            ],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )

        current, summary = build_catalog(
            [product("LIVE-1", productId=101)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        videos = {
            video["code"]: video
            for series in current["series"]
            for video in series["videos"]
        }
        self.assertEqual(sorted(videos), ["ARCH-1", "ARCH-2", "LIVE-1"])
        self.assertEqual(summary["counts"]["archiveRetained"], 2)
        self.assertEqual(summary["codes"]["archiveRetained"], ["ARCH-1", "ARCH-2"])
        self.assertNotIn("archiveRetained", current["refresh"]["counts"])

    def test_unbounded_authoritative_audit_replaces_archive_when_code_becomes_official(self) -> None:
        """An official same-code record must replace, rather than preserve, its archive copy."""
        previous, _ = build_catalog(
            [product("ARCH-1", title="Archive title", productId=None)],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )

        current, summary = build_catalog(
            [product("ARCH-1", title="Official title", productId=501)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        video = current["series"][0]["videos"][0]
        self.assertEqual(video["productId"], 501)
        self.assertEqual(video["title"], "Official title")
        self.assertEqual(summary["counts"]["archiveRetained"], 0)
        self.assertEqual(summary["codes"]["archiveRetained"], [])

    def test_unbounded_authoritative_audit_still_deletes_missing_official_records(self) -> None:
        """Archive retention must not weaken deletion behavior for official records."""
        previous, _ = build_catalog(
            [
                product("ARCH-1", productId=None),
                product("LIVE-1", productId=101),
                product("STALE-1", productId=102),
            ],
            {},
            generated_at="2026-07-28T00:00:00Z",
        )

        current, summary = build_catalog(
            [product("LIVE-1", productId=101)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )

        self.assertEqual(summary["codes"]["deleted"], ["STALE-1"])
        self.assertEqual(summary["codes"]["archiveRetained"], ["ARCH-1"])
        self.assertEqual(current["refresh"]["counts"]["deleted"], 1)

    def test_only_emits_compact_preview_descriptor(self) -> None:
        """Expanded preview URL arrays would bloat the deployable catalog."""
        catalog, _ = build_catalog(
            [
                product(
                    "SPSF-1",
                    previewBase="https://example.test/previews/",
                    previewCount=18,
                    previewUrls=["https://example.test/1.jpg"],
                )
            ],
            {},
            generated_at=GENERATED_AT,
        )

        video = catalog["series"][0]["videos"][0]
        self.assertEqual(video["previewCount"], 18)
        self.assertEqual(video["previewBase"], "https://example.test/previews/")
        self.assertNotIn("previewUrls", video)

    def test_embedded_refresh_counts_import_conflicts_without_diagnostics(self) -> None:
        """Public counts need the stable conflict total, not volatile row diagnostics."""
        catalog, _ = build_catalog(
            [product("SPSF-1")],
            {},
            generated_at=GENERATED_AT,
            refresh_context={
                "mode": "links-only",
                "scanComplete": True,
                "linkConflicts": 2,
            },
        )

        self.assertEqual(catalog["refresh"]["counts"]["linkConflicts"], 2)
        self.assertNotIn("diagnostics", catalog["refresh"])

    def test_output_is_stable_under_full_input_permutations(self) -> None:
        """A shuffled source must produce exactly the same compact UTF-8 document."""
        products = [
            product("A-1", title="日本語"),
            product("A-2"),
            product("B-1", date="2025-01-01"),
        ]
        links = {
            "A-1": {"gofile": "https://example.test/a"},
            "B-1": {"player4me": "https://example.test/b"},
        }
        expected, expected_summary = build_catalog(
            products, links, generated_at=GENERATED_AT
        )
        expected_bytes = serialize_catalog(expected)
        expected_summary_bytes = json.dumps(
            expected_summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        for seed in range(5):
            shuffled_products = copy.deepcopy(products)
            random.Random(seed).shuffle(shuffled_products)
            shuffled_links = dict(reversed(list(links.items()))) if seed % 2 else links
            actual, actual_summary = build_catalog(
                shuffled_products,
                shuffled_links,
                generated_at=GENERATED_AT,
            )
            self.assertEqual(serialize_catalog(actual), expected_bytes)
            self.assertEqual(
                json.dumps(
                    actual_summary,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                expected_summary_bytes,
            )


@unittest.skipUnless(
    (LEGACY_DIR / "data.json").is_file() and (LEGACY_DIR / "links.json").is_file(),
    "real read-only legacy source is unavailable",
)
class RealLegacyMergeSmokeTests(unittest.TestCase):
    def test_real_legacy_source_builds_expected_counts_without_mutation(self) -> None:
        """Blank legacy providers must not block the first real links-only refresh."""
        before = {
            name: (LEGACY_DIR / name).stat().st_mtime_ns
            for name in ("data.json", "links.json")
        }
        products, links = migrate_legacy(
            LEGACY_DIR / "data.json", LEGACY_DIR / "links.json"
        )
        catalog, _ = build_catalog(products, links, generated_at=GENERATED_AT)
        legacy_document = json.loads(
            (LEGACY_DIR / "data.json").read_text(encoding="utf-8")
        )

        self.assertEqual(len(products), 3200)
        self.assertEqual(len(links), 2983)
        self.assertEqual(legacy_document["totalSeries"], 118)
        self.assertEqual(
            catalog["totals"],
            {
                # TSWN is the one declared legacy series with no products.
                "series": 117,
                "videos": 3200,
                "linkedVideos": 2944,
            },
        )
        self.assertEqual(
            {
                name: (LEGACY_DIR / name).stat().st_mtime_ns
                for name in ("data.json", "links.json")
            },
            before,
        )


if __name__ == "__main__":
    unittest.main()
