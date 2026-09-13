import unittest

from src.giga_catalog.archived_import import (
    build_archived_products,
    merge_archived_catalog,
    parse_archived_sheet_links,
)
from src.giga_catalog.merge import build_catalog


class ArchivedImportTests(unittest.TestCase):
    def test_merge_replaces_a_previous_archived_snapshot_with_refreshed_previews(self) -> None:
        previous, _ = build_catalog(
            [
                {
                    "code": "GIRO-92",
                    "title": "Archived",
                    "actors": [],
                    "releaseDate": None,
                    "cover": "https://www.giga-web.jp/db_titles/giro/giro92/pac_s.jpg",
                    "previewImages": [
                        "https://www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_01.jpg"
                    ],
                }
            ],
            {},
            generated_at="2026-09-12T00:00:00Z",
        )
        archived = [
            {
                "code": "GIRO-92",
                "title": "Archived",
                "actors": [],
                "releaseDate": None,
                "cover": "https://www.giga-web.jp/db_titles/giro/giro92/pac_s.jpg",
                "previewImages": [
                    "https://i0.wp.com/www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_01.jpg"
                ],
            }
        ]

        current, _ = merge_archived_catalog(
            previous,
            archived,
            {},
            generated_at="2026-09-13T00:00:00Z",
        )

        self.assertEqual(
            current["series"][0]["videos"][0]["previewImages"],
            archived[0]["previewImages"],
        )

    def test_merge_adds_archived_records_and_preserves_existing_catalog(self) -> None:
        previous, _ = build_catalog(
            [
                {
                    "code": "GIRO-91",
                    "title": "Existing",
                    "actors": [],
                    "releaseDate": "2015-05-01",
                    "cover": "https://www.giga-web.jp/db_titles/giro/giro91/pac_s.jpg",
                }
            ],
            {},
            generated_at="2026-09-12T00:00:00Z",
        )
        archived = [
            {
                "code": "GIRO-92",
                "title": "Archived",
                "actors": [],
                "releaseDate": None,
                "cover": "https://www.giga-web.jp/db_titles/giro/giro92/pac_s.jpg",
                "previewImages": [
                    "https://www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_01.jpg"
                ],
            }
        ]

        current, summary = merge_archived_catalog(
            previous,
            archived,
            {"GIRO-92": {"uncensored": {"gofile": "https://ouo.io/file"}}},
            generated_at="2026-09-13T00:00:00Z",
        )

        videos = {
            video["code"]: video
            for series in current["series"]
            for video in series["videos"]
        }
        self.assertEqual(set(videos), {"GIRO-91", "GIRO-92"})
        self.assertEqual(videos["GIRO-92"]["links"]["uncensored"]["gofile"], "https://ouo.io/file")
        self.assertEqual(summary["counts"]["added"], 1)

    def test_builds_unknown_date_products_with_official_covers_and_all_previews(self) -> None:
        confirmed = [
            {
                "规范番号": "GIRO-92",
                "标题": "GIRO-92 Sailor Legend V Part 2",
                "官方资源HEAD地址": "https://www.giga-web.jp/db_titles/giro/giro92/pac_s.jpg",
                "AsiaMonstr详情页": "https://www.asiamonstr.com/giga/giro-92.html",
            }
        ]
        details = {
            "GIRO-92": {
                "detailUrl": "https://www.asiamonstr.com/giga/giro-92.html",
                "previewImages": [
                    "https://www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_01.jpg",
                    "https://www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_02.jpg",
                ],
            }
        }

        self.assertEqual(
            build_archived_products(confirmed, details),
            [
                {
                    "actors": [],
                    "code": "GIRO-92",
                    "cover": "https://www.giga-web.jp/db_titles/giro/giro92/pac_s.jpg",
                    "previewImages": [
                        "https://i0.wp.com/www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_01.jpg",
                        "https://i0.wp.com/www.asiamonstr.com/wp-content/uploads/2015/05/GIRO92_02.jpg",
                    ],
                    "releaseDate": None,
                    "title": "GIRO-92 Sailor Legend V Part 2",
                }
            ],
        )

    def test_reads_links_from_the_code_named_in_the_uncensored_column(self) -> None:
        sheet = (
            "NEW CODE,STREAMTAPE LINK,GOFILE LINK,,UNCENSORED,STREAMTAPE LINK,GOFILE LINK\n"
            "SPSF-64,https://normal/st,https://normal/gf,,GIRO-92 UMR.mp4,"
            "https://ouo.io/uncensored-st,https://ouo.io/uncensored-gf\n"
        )

        self.assertEqual(
            parse_archived_sheet_links(sheet, {"GIRO-92"}),
            {
                "GIRO-92": {
                    "uncensored": {
                        "gofile": "https://ouo.io/uncensored-gf",
                        "streamtape": "https://ouo.io/uncensored-st",
                    }
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
