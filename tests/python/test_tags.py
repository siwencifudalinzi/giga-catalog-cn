from pathlib import Path
import unittest

from src.giga_catalog.tags import (
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


if __name__ == "__main__":
    unittest.main()
