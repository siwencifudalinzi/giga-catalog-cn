import sys
import unittest
from pathlib import Path


AUDIT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUDIT_DIR))

import verify_delisted


class OfficialAssetEvidenceTests(unittest.TestCase):
    def test_builds_preferred_width_then_bounded_fallback_urls(self):
        self.assertEqual(
            verify_delisted.cover_probe_urls("GHNU-8", preferred_width=2),
            [
                "https://www.giga-web.jp/db_titles/ghnu/ghnu08/pac_s.jpg",
                "https://www.giga-web.jp/db_titles/ghnu/ghnu8/pac_s.jpg",
                "https://www.giga-web.jp/db_titles/ghnu/ghnu008/pac_s.jpg",
            ],
        )

    def test_successful_official_head_plus_current_absence_confirms_removal(self):
        result = verify_delisted.verification_label(
            asia_evidence=True,
            current_directory_absent=True,
            asset_status=200,
            content_type="image/jpeg",
        )
        self.assertEqual(result, "确认当前官网目录已移除（官方资源残留）")

    def test_failed_asset_probe_does_not_become_confirmed_delisting(self):
        result = verify_delisted.verification_label(
            asia_evidence=True,
            current_directory_absent=True,
            asset_status=404,
            content_type="text/html",
        )
        self.assertEqual(result, "当前官网目录缺失，只有第三方历史线索")


if __name__ == "__main__":
    unittest.main()
