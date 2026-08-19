import unittest

from src.giga_catalog.codes import normalize_code


class NormalizeCodeTests(unittest.TestCase):
    def test_normalizes_supported_prefix_and_separator_variants(self) -> None:
        """Catalog codes must share one uppercase, unpadded canonical key."""
        cases = {
            "spsf-44": "SPSF-44",
            "SPSF 044": "SPSF-44",
            "spsf_0044": "SPSF-44",
            "g1_0007": "G1-7",
            "  spsf-00044  ": "SPSF-44",
            "THZA-00": "THZA-0",
            " thza_000 ": "THZA-0",
        }

        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_code(value), expected)

    def test_rejects_non_codes_and_invalid_suffixes(self) -> None:
        """Explanatory, incomplete, empty, negative, and missing suffixes stay invalid."""
        for value in (
            "SPSF-44 (uncensored)",
            "SPSF",
            "",
            "   ",
            "SPSF--1",
            "SPSF-",
        ):
            with self.subTest(value=value):
                self.assertIsNone(normalize_code(value))


if __name__ == "__main__":
    unittest.main()
