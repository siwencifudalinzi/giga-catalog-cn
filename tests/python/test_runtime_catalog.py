import json
import unittest

from src.giga_catalog.runtime_catalog import build_runtime_catalogs


class RuntimeCatalogTests(unittest.TestCase):
    def test_splits_tags_from_core_without_mutating_the_full_catalog(self):
        full = {
            "schemaVersion": 1,
            "generatedAt": "2026-08-21T00:00:00Z",
            "totals": {"videos": 2, "series": 1, "linkedVideos": 0},
            "tags": [
                {
                    "id": 10,
                    "group": "genre",
                    "nameJa": "黒ストッキング",
                    "nameZh": "黑丝袜",
                    "count": 1,
                }
            ],
            "series": [
                {
                    "code": "SPSF",
                    "videos": [
                        {
                            "code": "SPSF-2",
                            "tagIds": [],
                            "tagsStatus": "complete",
                            "tagsUpdatedAt": "2026-08-21T00:00:00Z",
                            "tagsSource": "official",
                        },
                        {
                            "code": "SPSF-1",
                            "tagIds": [10],
                            "tagsStatus": "complete",
                            "tagsUpdatedAt": "2026-08-21T00:00:00Z",
                            "tagsSource": "official",
                        },
                    ],
                }
            ],
        }

        core, tag_payload = build_runtime_catalogs(full)

        self.assertIn("tags", full)
        self.assertIn("tagIds", full["series"][0]["videos"][0])
        self.assertNotIn("tags", core)
        for video in core["series"][0]["videos"]:
            self.assertFalse(
                {"tagIds", "tagsStatus", "tagsUpdatedAt", "tagsSource"}
                & set(video)
            )
        self.assertEqual(
            tag_payload,
            {
                "schemaVersion": 1,
                "generatedAt": "2026-08-21T00:00:00Z",
                "tags": full["tags"],
                "assignments": [["SPSF-1", [10]]],
            },
        )

    def test_real_core_is_materially_smaller_than_the_complete_payload(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        full = json.loads(
            (root / "public" / "data" / "catalog.json").read_text(encoding="utf-8")
        )

        core, tag_payload = build_runtime_catalogs(full)
        compact = lambda value: json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

        self.assertLess(len(compact(core)), len(compact(full)) * 0.7)
        self.assertEqual(
            sum(len(tag_ids) for _, tag_ids in tag_payload["assignments"]),
            sum(
                len(video.get("tagIds", []))
                for series in full["series"]
                for video in series["videos"]
            ),
        )


if __name__ == "__main__":
    unittest.main()
