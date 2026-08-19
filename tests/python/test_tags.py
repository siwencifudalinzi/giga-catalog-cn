from pathlib import Path
import unittest

from src.giga_catalog.tags import (
    build_public_tag_index,
    normalize_tag_definitions,
    parse_product_tags,
    parse_tag_directory,
    product_detail_headers,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class ProductTagParserTests(unittest.TestCase):
    def test_parses_only_official_genre_and_character_tag_groups(self):
        html = (FIXTURES / "product_tags.html").read_text(encoding="utf-8")

        self.assertEqual(
            parse_product_tags(html),
            [
                {"id": 6, "group": "genre", "nameJa": "陰落"},
                {"id": 25, "group": "genre", "nameJa": "黒髪"},
                {"id": 30256, "group": "character", "nameJa": "戦隊"},
                {"id": 2342, "group": "character", "nameJa": "戦隊ピンク"},
            ],
        )

    def test_parses_directory_tags_for_the_requested_group(self):
        html = """
        <main>
          <a href="./index.php?tag_id=6">陰落</a>
          <a href="index.php?tag_id=25">黒髪</a>
          <a href="index.php?tag_id=6">陰落</a>
          <a href="index.php?actor_id=1">女優</a>
        </main>
        """

        self.assertEqual(
            parse_tag_directory(html, "genre"),
            [
                {"id": 6, "group": "genre", "nameJa": "陰落"},
                {"id": 25, "group": "genre", "nameJa": "黒髪"},
            ],
        )

    def test_product_detail_headers_use_same_site_referer(self):
        self.assertEqual(
            product_detail_headers("https://www.giga-web.jp"),
            {"Referer": "https://www.giga-web.jp/search/"},
        )

    def test_rejects_unknown_directory_group(self):
        with self.assertRaisesRegex(ValueError, "unknown tag group"):
            parse_tag_directory("<html></html>", "other")


class TagIndexTests(unittest.TestCase):
    def test_normalizes_unique_ids_and_prefers_reviewed_chinese_overrides(self):
        definitions = [
            {"id": 25, "group": "genre", "nameJa": "黒髪", "nameZh": "黑色头发"},
            {"id": 6, "group": "genre", "nameJa": "陰落", "nameZh": "秋天"},
            {"id": 25, "group": "genre", "nameJa": "黒髪"},
        ]

        self.assertEqual(
            normalize_tag_definitions(
                definitions,
                {"陰落": "沦陷", "黒髪": "黑发"},
            ),
            [
                {
                    "id": 6,
                    "group": "genre",
                    "nameJa": "陰落",
                    "nameZh": "沦陷",
                    "translationSource": "reviewed",
                },
                {
                    "id": 25,
                    "group": "genre",
                    "nameJa": "黒髪",
                    "nameZh": "黑发",
                    "translationSource": "reviewed",
                },
            ],
        )

    def test_builds_counts_and_rejects_unresolved_video_tag_ids(self):
        definitions = [
            {"id": 6, "group": "genre", "nameJa": "陰落", "nameZh": "沦陷"},
            {"id": 25, "group": "genre", "nameJa": "黒髪", "nameZh": "黑发"},
        ]
        products = [
            {"code": "SPSF-1", "tagIds": [25, 6, 6]},
            {"code": "SPSF-2", "tagIds": [6]},
        ]

        self.assertEqual(
            build_public_tag_index(products, definitions),
            [
                {"id": 6, "group": "genre", "nameJa": "陰落", "nameZh": "沦陷", "count": 2},
                {"id": 25, "group": "genre", "nameJa": "黒髪", "nameZh": "黑发", "count": 1},
            ],
        )
        with self.assertRaisesRegex(ValueError, "SPSF-3.*999"):
            build_public_tag_index(
                [{"code": "SPSF-3", "tagIds": [999]}],
                definitions,
            )


if __name__ == "__main__":
    unittest.main()
