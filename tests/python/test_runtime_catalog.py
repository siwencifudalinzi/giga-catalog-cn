import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build_runtime_catalog import build_runtime_from_catalog
from src.giga_catalog.runtime_catalog import build_runtime_catalogs, build_runtime_v3


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class RuntimeCatalogTests(unittest.TestCase):
    @staticmethod
    def _catalog():
        return {
            "schemaVersion": 1,
            "generatedAt": "2026-08-21T00:00:00Z",
            "totals": {"videos": 2, "series": 2, "linkedVideos": 1},
            "refresh": {
                "mode": "incremental",
                "sourceComplete": True,
                "counts": {
                    "added": 2,
                    "updated": 0,
                    "retained": 0,
                    "deleted": 0,
                    "linked": 1,
                    "linkAdded": 1,
                    "linkUpdated": 0,
                    "linkRemoved": 0,
                    "linkConflicts": 0,
                },
            },
            "resources": {
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://drive.google.com/drive/folders/abc",
                }
            },
            "tags": [
                {
                    "id": 10,
                    "group": "genre",
                    "nameJa": "アクション",
                    "nameZh": "动作",
                    "count": 2,
                }
            ],
            "series": [
                {
                    "code": "SPSF",
                    "count": 1,
                    "firstReleaseDate": "2026-08-01",
                    "latestReleaseDate": "2026-08-02",
                    "links": {"subtitle": "https://drive.google.com/file/d/spsf"},
                    "videos": [
                        {
                            "code": "SPSF-1",
                            "number": 1,
                            "title": "SPSF one",
                            "actors": ["B"],
                            "releaseDate": "2026-08-01",
                            "cover": "https://example.test/spsf-1.jpg",
                            "links": {"gofile": "https://example.test/spsf-1"},
                            "tagIds": [10],
                            "tagsStatus": "complete",
                            "tagsUpdatedAt": "2026-08-21T00:00:00Z",
                            "tagsSource": "official",
                        },
                    ],
                },
                {
                    "code": "NEWS",
                    "count": 1,
                    "firstReleaseDate": "2026-08-03",
                    "latestReleaseDate": "2026-08-03",
                    "videos": [
                        {
                            "code": "NEWS-1",
                            "number": 1,
                            "title": "News one",
                            "actors": ["C"],
                            "releaseDate": "2026-08-03",
                            "cover": "https://example.test/news-1.jpg",
                            "tagIds": [10],
                            "tagsStatus": "complete",
                            "tagsUpdatedAt": "2026-08-21T00:00:00Z",
                            "tagsSource": "official",
                        }
                    ],
                },
            ],
        }

    def test_v3_builds_bootstrap_search_tags_and_one_shard_per_series(self):
        bundle = build_runtime_v3(self._catalog())
        self.assertRegex(bundle.generation, r"^[0-9a-f]{64}$")
        self.assertEqual(bundle.bootstrap["schemaVersion"], 3)
        self.assertEqual(bundle.bootstrap["generation"], bundle.generation)
        self.assertEqual(len(bundle.bootstrap["recentVideos"]), 2)
        self.assertEqual(
            [item["code"] for item in bundle.bootstrap["series"]],
            ["NEWS", "SPSF"],
        )
        paths = [path for path, _ in bundle.files]
        prefix = f"runtime/g/{bundle.generation}/"
        self.assertEqual(
            paths,
            [
                prefix + "search.json",
                prefix + "tags.json",
                prefix + "series/news.json",
                prefix + "series/spsf.json",
            ],
        )

        search = bundle.files[0][1]
        self.assertEqual([item["code"] for item in search["videos"]], ["NEWS-1", "SPSF-1"])
        self.assertEqual(search["videos"][0]["series"], "NEWS")
        self.assertNotIn("tagsStatus", search["videos"][0])
        tags = bundle.files[1][1]
        self.assertEqual(tags["assignments"], [["NEWS-1", [10]], ["SPSF-1", [10]]])
        shard = bundle.files[2][1]
        self.assertEqual(shard["series"]["code"], "NEWS")
        self.assertEqual([video["code"] for video in shard["series"]["videos"]], ["NEWS-1"])

    def test_v3_is_deterministic_and_covers_canonical_videos(self):
        first = self._catalog()
        second = copy.deepcopy(first)
        second["series"] = [dict(reversed(series.items())) for series in reversed(second["series"])]
        for series in second["series"]:
            series["videos"] = [dict(reversed(video.items())) for video in reversed(series["videos"])]

        bundle = build_runtime_v3(first)
        self.assertEqual(bundle, build_runtime_v3(second))
        all_shard_codes = [
            video["code"]
            for path, payload in bundle.files
            if "/series/" in path
            for video in payload["series"]["videos"]
        ]
        canonical_codes = [
            video["code"]
            for series in first["series"]
            for video in series["videos"]
        ]
        self.assertEqual(sorted(all_shard_codes), sorted(canonical_codes))
        self.assertEqual(len(all_shard_codes), len(set(all_shard_codes)))
        compact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(compact(bundle.bootstrap)), 250 * 1024)

    def test_v3_rejects_invalid_runtime_inputs(self):
        cases = []
        duplicate_series = self._catalog()
        duplicate_series["series"].append(copy.deepcopy(duplicate_series["series"][0]))
        cases.append((duplicate_series, "duplicate series code"))

        unsafe_code = self._catalog()
        unsafe_code["series"][0]["code"] = "../SPSF"
        cases.append((unsafe_code, "unsafe series code"))

        duplicate_video = self._catalog()
        duplicate_video["series"][1]["videos"][0]["code"] = "SPSF-1"
        cases.append((duplicate_video, "duplicate video code"))

        bad_count = self._catalog()
        bad_count["series"][0]["count"] = 3
        cases.append((bad_count, "count mismatch"))

        for catalog, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    build_runtime_v3(catalog)

        with self.assertRaisesRegex(ValueError, "recent_limit must be a positive integer"):
            build_runtime_v3(self._catalog(), recent_limit=0)

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
        root = REPOSITORY_ROOT
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


class RuntimeCatalogBuilderTests(unittest.TestCase):
    def test_invalid_catalog_does_not_change_existing_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text("{}", encoding="utf-8")
            paths = self._runtime_paths(root)
            paths["runtime_bootstrap"].parent.mkdir(parents=True)
            paths["runtime_bootstrap"].write_text("old bootstrap", encoding="utf-8")
            (paths["runtime_root"] / "g" / ("a" * 64)).mkdir(parents=True)
            before = self._snapshot(root)

            with self.assertRaisesRegex(RuntimeError, "catalog validation failed"):
                build_runtime_from_catalog(catalog_path, **paths)

            self.assertEqual(self._snapshot(root), before)

    def test_republishes_byte_identical_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_bytes(
                (REPOSITORY_ROOT / "public" / "data" / "catalog.json").read_bytes()
            )
            paths = self._runtime_paths(root)

            first = build_runtime_from_catalog(catalog_path, **paths)
            first_bytes = self._snapshot(root)
            second = build_runtime_from_catalog(catalog_path, **paths)

            self.assertEqual(first, second)
            self.assertEqual(self._snapshot(root), first_bytes)

    def test_replacement_failure_preserves_bootstrap_and_generation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_bytes(
                (REPOSITORY_ROOT / "public" / "data" / "catalog.json").read_bytes()
            )
            paths = self._runtime_paths(root)
            build_runtime_from_catalog(catalog_path, **paths)
            before = self._snapshot(root)

            from scripts.refresh import _commit_transaction as real_commit_transaction

            def fail_replacement(operations, replacer, stale_remover):
                return real_commit_transaction(
                    operations,
                    replacer=lambda source, target: (_ for _ in ()).throw(
                        OSError("injected replacement failure")
                    ),
                    stale_remover=stale_remover,
                )

            with mock.patch(
                "scripts.build_runtime_catalog._commit_transaction",
                side_effect=fail_replacement,
            ), self.assertRaisesRegex(OSError, "injected replacement failure"):
                build_runtime_from_catalog(catalog_path, **paths)

            self.assertEqual(self._snapshot(root), before)

    @staticmethod
    def _runtime_paths(root):
        data = root / "public" / "data"
        return {
            "runtime_core": data / "catalog-core.json",
            "runtime_tags": data / "catalog-tags.json",
            "runtime_bootstrap": data / "catalog-bootstrap.json",
            "runtime_root": data / "runtime",
        }

    @staticmethod
    def _snapshot(root):
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
