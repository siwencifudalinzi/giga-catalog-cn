import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


AUDIT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(AUDIT_DIR))
sys.path.insert(0, str(REPO_ROOT))

import audit


class CodeNormalizationTests(unittest.TestCase):
    def test_normalizes_full_width_case_dash_and_leading_zeroes(self):
        cases = {
            " ｓｐｓｆ－００６ ": "SPSF-6",
            "thza_000": "THZA-0",
            "YNO ０１２": "YNO-12",
            "ab–09": "AB-9",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(audit.normalize_audit_code(raw), expected)

    def test_extracts_only_explicit_code_shapes_not_dates_or_episode_counts(self):
        text = "発売 2024-01-02 / 全12話 / 番号：SPSF-006 and abc_09 / Part 2 / Vol 116"
        self.assertEqual(
            audit.extract_explicit_codes(text),
            [("SPSF-006", "SPSF-6"), ("abc_09", "ABC-9")],
        )

    def test_category_heading_uses_only_the_leading_code_field(self):
        self.assertEqual(
            audit.extract_leading_code("SPSD-90 Shiranui S-3 Airborne Special Task Force"),
            ("SPSD-90", "SPSD-90"),
        )
        self.assertIsNone(audit.extract_leading_code("Heroine title without code"))


class BaselineIndexTests(unittest.TestCase):
    def test_audit_timestamp_is_explicitly_asia_shanghai_without_tzdata_dependency(self):
        self.assertRegex(audit.audit_timestamp(), r"\+08:00$")

    def test_indexes_every_series_and_bounds_gaps_by_observed_minimum_and_maximum(self):
        catalog = {
            "series": [
                {"code": "AAA", "videos": [{"code": "AAA-2"}, {"code": "AAA-4"}]},
                {"code": "BBB", "videos": [{"code": "BBB-9"}]},
            ]
        }
        index = audit.build_baseline_index(catalog)
        self.assertEqual(index["AAA"]["codes"], {"AAA-2", "AAA-4"})
        self.assertEqual(index["AAA"]["gaps"], ["AAA-3"])
        self.assertEqual(index["BBB"]["gaps"], [])

    def test_snapshot_metadata_hashes_exact_bytes_and_cross_checks_parsed_totals(self):
        raw = json.dumps(
            {"series": [{"code": "AAA", "videos": [{"code": "AAA-1"}]}]},
            ensure_ascii=False,
        ).encode("utf-8")
        metadata = audit.snapshot_metadata(raw, "2026-09-12T10:00:00+08:00")
        self.assertEqual(metadata["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(metadata["seriesCount"], 1)
        self.assertEqual(metadata["videoCount"], 1)


class EvidenceTests(unittest.TestCase):
    def test_deduplicates_movies_without_losing_multiple_source_records(self):
        rows = [
            {"raw_code": "AAA-01", "canonical_code": "AAA-1", "source": "sheet", "url": "s"},
            {"raw_code": "aaa-1", "canonical_code": "AAA-1", "source": "asiamonstr", "url": "a"},
        ]
        merged = audit.group_evidence(rows)
        self.assertEqual(list(merged), ["AAA-1"])
        self.assertEqual(len(merged["AAA-1"]), 2)

    def test_explicit_source_above_current_maximum_remains_a_candidate(self):
        baseline = {"AAA": {"codes": {"AAA-1", "AAA-2"}, "gaps": []}}
        evidence = [{"raw_code": "AAA-9", "canonical_code": "AAA-9", "source": "sheet"}]
        candidates = audit.build_candidates(baseline, evidence)
        self.assertIn("AAA-9", candidates)

    def test_classification_never_turns_empty_slot_or_access_failure_into_delisting(self):
        self.assertEqual(audit.classify_candidate({"sources": ["gap"]})[0], "无证据空号")
        self.assertEqual(
            audit.classify_candidate({"sources": ["history"], "official_status": "timeout"})[0],
            "历史条目，状态待核实",
        )
        self.assertEqual(
            audit.classify_candidate({"sources": ["asiamonstr"], "official_status": "confirmed"})[0],
            "确认漏收录",
        )
        self.assertEqual(
            audit.classify_candidate(
                {"sources": ["history"], "official_status": "delisted", "official_delist_evidence": True}
            )[0],
            "确认官方下架",
        )

    def test_sheet_rows_preserve_source_location_and_raw_code(self):
        rows = audit.parse_sheet_candidates(
            "NEW CODE,STREAMTAPE LINK\nAAA-09,https://example.test/a\nZZZ-1,\n"
        )
        self.assertEqual(
            rows[0],
            {
                "source": "sheet",
                "raw_code": "AAA-09",
                "canonical_code": "AAA-9",
                "sheet_location": "row 2",
            },
        )

    def test_historical_snapshots_report_only_codes_absent_from_baseline(self):
        baseline = {"AAA": {"codes": {"AAA-1"}, "gaps": []}}
        rows = audit.history_candidates(
            baseline,
            [("abc123", {"series": [{"code": "AAA", "videos": [{"code": "AAA-1"}, {"code": "AAA-2"}]}]})],
        )
        self.assertEqual([row["canonical_code"] for row in rows], ["AAA-2"])
        self.assertEqual(rows[0]["history_location"], "abc123:public/data/catalog.json")


class OutputContractTests(unittest.TestCase):
    def test_script_starts_from_its_own_directory_without_pythonpath(self):
        completed = subprocess.run(
            [sys.executable, str(AUDIT_DIR / "audit.py"), "--help"],
            cwd=AUDIT_DIR,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--audit-root", completed.stdout)

    def test_writes_required_columns_and_every_baseline_series(self):
        baseline_meta = {
            "sourceUrl": "https://catalog.test/catalog.json",
            "fetchedAt": "2026-09-12T10:00:00+08:00",
            "sha256": "abc",
            "seriesCount": 2,
            "videoCount": 2,
            "catalog": {
                "series": [
                    {"code": "AAA", "videos": [{"code": "AAA-1"}]},
                    {"code": "BBB", "videos": [{"code": "BBB-2"}]},
                ]
            },
        }
        candidates = {
            "BBB-1": {
                "canonical_code": "BBB-1",
                "series": "BBB",
                "sources": ["gap"],
                "evidence": [],
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            audit.write_outputs(
                Path(directory), baseline_meta, candidates, [], {"completed": False, "failure": "page 2 failed"}
            )
            series_text = (Path(directory) / "series-coverage.csv").read_text(encoding="utf-8-sig")
            missing_text = (Path(directory) / "missing-records.csv").read_text(encoding="utf-8-sig")
            self.assertIn("AAA", series_text)
            self.assertIn("BBB", series_text)
            for column in audit.MISSING_COLUMNS:
                self.assertIn(column, missing_text.splitlines()[0])
            report = (Path(directory) / "audit-report.md").read_text(encoding="utf-8")
            self.assertIn("AsiaMonstr 分类遍历未完成", report)
            self.assertIn("page 2 failed", report)


if __name__ == "__main__":
    unittest.main()
