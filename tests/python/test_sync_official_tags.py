import unittest

from scripts.sync_official_tags import (
    apply_product_detail,
    apply_product_id_overrides,
    mark_unavailable_product_tags,
    merge_tag_definitions,
    select_tag_sync_targets,
)


STAMP = "2026-08-20T00:00:00Z"


class OfficialTagSyncTests(unittest.TestCase):
    def test_applies_authoritative_detail_and_marks_even_empty_tags_complete(self):
        existing = {
            "code": "SPSF-1",
            "title": "Old",
            "actors": ["Old Actor"],
            "releaseDate": "2026-01-01",
            "cover": "https://example.test/old.jpg",
            "links": {"gofile": "https://example.test/file"},
        }
        detail = {
            "productId": 7678,
            "code": "SPSF-1",
            "series": "SPSF",
            "number": 1,
            "title": "Official",
            "actors": ["Official Actor"],
            "releaseDate": "2026-04-24",
            "cover": "https://www.giga-web.jp/cover.jpg",
            "previewBase": "https://www.giga-web.jp/sample/",
            "previewCount": 18,
        }

        enriched = apply_product_detail(existing, detail, [], STAMP)

        self.assertEqual(enriched["title"], "Official")
        self.assertEqual(enriched["links"], existing["links"])
        self.assertEqual(enriched["tagIds"], [])
        self.assertEqual(enriched["tagsStatus"], "complete")
        self.assertEqual(enriched["tagsUpdatedAt"], STAMP)
        self.assertEqual(enriched["tagsSource"], "official")

    def test_rejects_detail_for_a_different_catalog_code(self):
        with self.assertRaisesRegex(ValueError, "code mismatch"):
            apply_product_detail(
                {"code": "SPSF-1"},
                {"code": "SPSF-2"},
                [],
                STAMP,
            )

    def test_selects_missing_failed_and_stale_records_but_not_fresh_complete(self):
        records = [
            {"code": "A-1", "productId": 1},
            {"code": "A-2", "productId": 2, "tagsStatus": "pending"},
            {
                "code": "A-3",
                "productId": 3,
                "tagsStatus": "complete",
                "tagsUpdatedAt": "2026-01-01T00:00:00Z",
            },
            {
                "code": "A-4",
                "productId": 4,
                "tagsStatus": "complete",
                "tagsUpdatedAt": "2026-08-19T00:00:00Z",
            },
            {"code": "A-5"},
        ]

        selected = select_tag_sync_targets(
            records,
            now=STAMP,
            stale_days=90,
        )

        self.assertEqual([record["code"] for record in selected], ["A-1", "A-2", "A-3"])

    def test_applies_verified_product_id_overrides_without_overwriting_conflicts(self):
        records = [
            {"code": "LEGACY-1"},
            {"code": "KNOWN-2", "productId": 22},
        ]

        enriched = apply_product_id_overrides(
            records,
            {"LEGACY-1": 11, "KNOWN-2": 22},
        )

        self.assertEqual(enriched[0]["productId"], 11)
        self.assertEqual(enriched[1]["productId"], 22)
        with self.assertRaisesRegex(ValueError, "productId override conflict"):
            apply_product_id_overrides(records, {"KNOWN-2": 99})

    def test_marks_officially_unavailable_archive_record_complete_without_fabricating_tags(self):
        record = mark_unavailable_product_tags(
            {"code": "ARCHIVE-1", "title": "Archive"},
            STAMP,
        )

        self.assertEqual(record["tagIds"], [])
        self.assertEqual(record["tagsStatus"], "complete")
        self.assertEqual(record["tagsSource"], "official-unavailable")
        self.assertEqual(record["tagsUpdatedAt"], STAMP)

        unchanged = mark_unavailable_product_tags(record, "2026-08-21T00:00:00Z")
        self.assertEqual(unchanged["tagsUpdatedAt"], STAMP)

    def test_preserves_referenced_detail_only_tags_missing_from_directory(self):
        directory = [
            {"id": 1, "group": "genre", "nameJa": "新名称"},
        ]
        stored = [
            {"id": 1, "group": "genre", "nameJa": "旧名称", "nameZh": "旧名称"},
            {"id": 2, "group": "character", "nameJa": "詳細限定", "nameZh": "详情限定"},
        ]

        merged = merge_tag_definitions(directory, stored)

        self.assertEqual([tag["id"] for tag in merged], [1, 2])
        self.assertEqual(merged[0]["nameJa"], "新名称")
        self.assertEqual(merged[1]["nameZh"], "详情限定")


if __name__ == "__main__":
    unittest.main()
