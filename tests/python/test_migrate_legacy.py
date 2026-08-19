import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_legacy import migrate_legacy


class MigrateLegacyTests(unittest.TestCase):
    def test_skips_links_with_invalid_codes(self) -> None:
        """A malformed link key must not enter the migrated link map."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            data_path = directory / "data.json"
            links_path = directory / "links.json"

            data_path.write_text(
                json.dumps([{"title": "Valid", "code": "spsf_0001"}]),
                encoding="utf-8",
            )
            links_path.write_text(
                json.dumps(
                    {
                        "spsf_0001": {"st": "streamtape-link"},
                        "not-a-catalog-code": {"gf": "invalid-link"},
                    }
                ),
                encoding="utf-8",
            )

            products, links = migrate_legacy(data_path, links_path)

        self.assertEqual(
            products,
            [{"title": "Valid", "code": "SPSF-1", "productId": None}],
        )
        self.assertEqual(
            links,
            {"SPSF-1": {"streamtape": "streamtape-link"}},
        )

    def test_skips_products_with_invalid_codes(self) -> None:
        """A malformed product code must not enter the migrated catalog."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            data_path = directory / "data.json"
            links_path = directory / "links.json"

            data_path.write_text(
                json.dumps(
                    {
                        "series": {
                            "SPSF": {
                                "videos": {
                                    "01": {"title": "Valid", "code": "spsf_0001"},
                                    "02": {
                                        "title": "Invalid",
                                        "code": "not-a-catalog-code",
                                    },
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            links_path.write_text(
                json.dumps({"spsf_0001": {"st": "streamtape-link"}}),
                encoding="utf-8",
            )

            products, links = migrate_legacy(data_path, links_path)

        self.assertEqual(
            products,
            [{"title": "Valid", "code": "SPSF-1", "productId": None}],
        )
        self.assertEqual(
            links,
            {"SPSF-1": {"streamtape": "streamtape-link"}},
        )

    def test_flattens_nested_series_and_normalizes_product_and_link_codes(self) -> None:
        """A migration must flatten real legacy series data under one canonical code."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            data_path = directory / "data.json"
            links_path = directory / "links.json"

            data_path.write_text(
                json.dumps(
                    {
                        "series": {
                            "SPSF": {
                                "videos": {
                                    "01": {
                                        "title": "Nested Title",
                                        "actors": ["Nested Actor"],
                                        "cover": "https://example.test/nested.jpg",
                                        "date": "2026-07-29",
                                        "code": "spsf_0044",
                                    },
                                    "02": None,
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            links_path.write_text(
                json.dumps(
                    {"spsf_0044": {"st": "streamtape-link", "gf": "gofile-link"}}
                ),
                encoding="utf-8",
            )

            try:
                products, links = migrate_legacy(data_path, links_path)
            except (TypeError, ValueError) as error:
                self.fail(f"nested legacy catalogs must migrate successfully: {error}")

        self.assertEqual(
            products,
            [
                {
                    "title": "Nested Title",
                    "actors": ["Nested Actor"],
                    "cover": "https://example.test/nested.jpg",
                    "date": "2026-07-29",
                    "code": "SPSF-44",
                    "productId": None,
                }
            ],
        )
        self.assertEqual(
            links,
            {"SPSF-44": {"streamtape": "streamtape-link", "gofile": "gofile-link"}},
        )

    def test_migrates_real_entries_and_normalizes_legacy_links(self) -> None:
        """A migration must discard null gaps and preserve the catalog payload."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            data_path = directory / "data.json"
            links_path = directory / "links.json"

            data_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Sample Title",
                            "actors": ["Sample Actor"],
                            "cover": "https://example.test/cover.jpg",
                            "date": "2026-07-29",
                            "code": "GIGA-001",
                        },
                        None,
                    ]
                ),
                encoding="utf-8",
            )
            links_path.write_text(
                json.dumps({"GIGA-001": {"st": "streamtape-link", "gf": "gofile-link"}}),
                encoding="utf-8",
            )

            products, links = migrate_legacy(data_path, links_path)

        self.assertEqual(
            products,
            [
                {
                    "title": "Sample Title",
                    "actors": ["Sample Actor"],
                    "cover": "https://example.test/cover.jpg",
                    "date": "2026-07-29",
                    "code": "GIGA-1",
                    "productId": None,
                }
            ],
        )
        self.assertEqual(
            links,
            {"GIGA-1": {"streamtape": "streamtape-link", "gofile": "gofile-link"}},
        )


if __name__ == "__main__":
    unittest.main()
