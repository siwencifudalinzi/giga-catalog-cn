import copy
import unittest

from src.giga_catalog.merge import build_catalog
from src.giga_catalog.validation import (
    DEFAULT_MIN_RELEASE_DATE,
    validate_catalog,
)


GENERATED_AT = "2026-07-29T00:00:00Z"


def product(code, date="2026-07-01", **overrides):
    value = {
        "code": code,
        "title": f"Title {code}",
        "actors": ["Actor"],
        "releaseDate": date,
        "cover": "https://example.test/cover.jpg",
    }
    value.update(overrides)
    return value


def catalog_for(products, links=None):
    return build_catalog(
        products,
        links or {},
        generated_at=GENERATED_AT,
    )[0]


def video_map(catalog):
    return {
        video["code"]: video
        for series in catalog["series"]
        for video in series["videos"]
    }


class CatalogSchemaValidationTests(unittest.TestCase):
    def test_accepts_a_valid_catalog_without_requiring_links_by_default(self) -> None:
        """Legitimate unlinked legacy products must not make normal publication impossible."""
        catalog = catalog_for([product("SPSF-1")])

        self.assertEqual(validate_catalog(catalog), [])
        self.assertEqual(DEFAULT_MIN_RELEASE_DATE, "2007-12-07")

    def test_accepts_an_exact_canonical_zero_code_and_number_pair(self) -> None:
        """A real official zero suffix remains valid only as matching code and integer zero."""
        catalog = catalog_for([product("THZA-1", productId=7390)])
        video = video_map(catalog)["THZA-1"]
        video["code"] = "THZA-0"
        video["number"] = 0

        self.assertEqual(validate_catalog(catalog), [])

    def test_accepts_valid_video_series_and_catalog_subtitle_scopes(self) -> None:
        """Each trusted subtitle scope is part of the public catalog contract."""
        catalog = build_catalog(
            [product("SPSF-44")],
            {
                "SPSF-44": {
                    "subtitle": "https://drive.google.com/file/d/spsf44/view"
                }
            },
            generated_at=GENERATED_AT,
            series_links={
                "SPSF": {
                    "subtitle": "https://drive.google.com/open?id=spsf-series"
                }
            },
            resources={
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            },
        )[0]

        self.assertEqual(validate_catalog(catalog), [])

    def test_rejects_invalid_or_unknown_subtitle_resource_and_series_shapes(self) -> None:
        """Hand-edited public metadata must not bypass scope and URL validation."""
        base = catalog_for([product("SPSF-1")])
        mutations = (
            {"resources": {"unknown": {"url": "https://example.test/x"}}},
            {
                "resources": {
                    "subtitleDirectory": {
                        "label": "SRT ENGSUB DOWNLOAD",
                        "url": "ftp://example.test/subtitles",
                    }
                }
            },
            {
                "resources": {
                    "subtitleDirectory": {
                        "label": "SRT ENGSUB DOWNLOAD",
                        "url": "https://example.test/subtitles",
                        "unresolved": "PGHD",
                    }
                }
            },
            {"seriesLinks": {"gofile": "https://example.test/file"}},
            {"seriesLinks": {"subtitle": "https://example.test/file"}},
            {
                "seriesLinks": {
                    "subtitle": "https://drive.google.com/open?id=bad\nvalue"
                }
            },
        )

        for mutation in mutations:
            with self.subTest(mutation=mutation):
                catalog = copy.deepcopy(base)
                if "resources" in mutation:
                    catalog["resources"] = mutation["resources"]
                if "seriesLinks" in mutation:
                    catalog["series"][0]["links"] = mutation["seriesLinks"]
                self.assertNotEqual(validate_catalog(catalog), [])

    def test_rejects_invalid_video_subtitle_urls_but_accepts_http(self) -> None:
        """Subtitle is a video provider, but it retains the same safe URL boundary."""
        valid = catalog_for(
            [product("SPSF-1")],
            {"SPSF-1": {"subtitle": "https://example.test/subtitle.srt"}},
        )
        self.assertEqual(validate_catalog(valid), [])

        for url in ("ftp://example.test/subtitle.srt", "https://bad\nhost/file"):
            with self.subTest(url=url):
                invalid = copy.deepcopy(valid)
                video_map(invalid)["SPSF-1"]["links"]["subtitle"] = url
                self.assertNotEqual(validate_catalog(invalid), [])

    def test_rejects_duplicate_codes_and_series_code_number_mismatches(self) -> None:
        """A duplicate or a video mounted under the wrong series breaks global lookup."""
        catalog = catalog_for([product("SPSF-1"), product("OTHER-1")])
        duplicate = copy.deepcopy(catalog["series"][0]["videos"][0])
        catalog["series"][1]["videos"].append(duplicate)
        catalog["series"][1]["count"] += 1
        duplicate["number"] = 2

        errors = validate_catalog(catalog)

        self.assertTrue(any("duplicate code" in error for error in errors), errors)
        self.assertTrue(any("code/series/number mismatch" in error for error in errors), errors)

    def test_rejects_totals_mismatch_and_empty_catalog(self) -> None:
        """Declared totals must be derived from real series rather than trusted metadata."""
        catalog = catalog_for([product("SPSF-1")])
        catalog["totals"]["videos"] = 99
        empty = copy.deepcopy(catalog)
        empty["series"] = []
        empty["totals"] = {"series": 0, "videos": 0, "linkedVideos": 0}

        self.assertTrue(
            any("totals.videos mismatch" in error for error in validate_catalog(catalog))
        )
        self.assertTrue(any("catalog is empty" in error for error in validate_catalog(empty)))

    def test_rejects_invalid_dates_urls_providers_actors_and_empty_uncensored(self) -> None:
        """Malformed non-empty leaves must fail instead of being silently published."""
        cases = {
            "bad real date": {"releaseDate": "2026-02-30"},
            "bad cover": {"cover": "file:///tmp/cover.jpg"},
            "malformed actors": {"actors": ["Actor", ""]},
            "bad provider URL": {"links": {"gofile": "ftp://example.test/file"}},
            "unknown provider": {"links": {"mystery": "https://example.test/x"}},
            "empty uncensored": {"links": {"uncensored": {}}},
            "preview base without count": {
                "previewBase": "https://example.test/sample/"
            },
            "preview count without base": {"previewCount": 18},
            "off-origin preview": {
                "previewBase": "https://example.test/sample/",
                "previewCount": 18,
            },
            "excessive preview count": {
                "previewBase": (
                    "https://www.giga-web.jp/db_titles/spsf/x/sample/"
                ),
                "previewCount": 100,
            },
        }
        for label, mutation in cases.items():
            with self.subTest(label=label):
                catalog = catalog_for([product("SPSF-1")])
                video_map(catalog)["SPSF-1"].update(mutation)
                self.assertNotEqual(validate_catalog(catalog), [])

    def test_preview_base_must_exactly_match_the_official_cover_directory(self) -> None:
        """A valid GIGA origin alone must not let one product borrow another gallery."""
        cover = (
            "https://www.giga-web.jp/db_titles/spsf/spsf0048/pac_s.jpg"
        )
        expected_base = (
            "https://www.giga-web.jp/db_titles/spsf/spsf0048/sample/"
        )
        catalog = catalog_for([product("SPSF-1")])
        video = video_map(catalog)["SPSF-1"]
        video.update(
            cover=cover,
            previewBase=expected_base,
            previewCount=31,
        )
        self.assertEqual(validate_catalog(catalog), [])

        invalid_bases = (
            "http://www.giga-web.jp/db_titles/spsf/spsf0048/sample/",
            f"{expected_base}?page=1",
            f"{expected_base}#gallery",
            "https://www.giga-web.jp/db_titles/spsf/spsf0047/sample/",
        )
        for preview_base in invalid_bases:
            with self.subTest(preview_base=preview_base):
                invalid = copy.deepcopy(catalog)
                video_map(invalid)["SPSF-1"]["previewBase"] = preview_base
                errors = validate_catalog(invalid)
                self.assertTrue(
                    any("does not match cover" in error for error in errors),
                    errors,
                )

    def test_rejects_empty_provider_strings_and_mojibake_markers(self) -> None:
        """Blank leaves and known corruption markers must be caught at the release gate."""
        catalog = catalog_for([product("SPSF-1")])
        video = video_map(catalog)["SPSF-1"]
        video["title"] = "涓涓 broken \ufffd"
        video["actors"] = ["涔涔 actor"]
        video["links"] = {"streamtape": ""}

        errors = validate_catalog(catalog)

        self.assertTrue(any("mojibake" in error for error in errors), errors)
        self.assertTrue(any("empty link URL" in error for error in errors), errors)

    def test_rejects_both_observed_and_documented_repeated_mojibake_pairs(self) -> None:
        """The release gate covers both corruption alphabets found in the contract."""
        for marker in ("丐", "乓", "涓", "涔"):
            with self.subTest(marker=marker):
                catalog = catalog_for(
                    [product("SPSF-1", title=f"{marker}{marker} broken")]
                )
                self.assertTrue(
                    any("mojibake" in error for error in validate_catalog(catalog))
                )

    def test_rejects_whitespace_urls_and_noncanonical_series_order(self) -> None:
        """Loose URL parsing and input-order series would make output nondeterministic."""
        catalog = catalog_for(
            [
                product("OLD-1", date="2025-01-01"),
                product("NEW-1", date="2026-01-01"),
            ]
        )
        catalog["series"].reverse()
        video_map(catalog)["NEW-1"]["links"] = {
            "gofile": "https://exa mple.test/file"
        }
        catalog["totals"]["linkedVideos"] = 1

        errors = validate_catalog(catalog)

        self.assertTrue(any("not a valid HTTP(S) URL" in error for error in errors), errors)
        self.assertTrue(any("series are not sorted" in error for error in errors), errors)

    def test_rejects_every_present_non_positive_or_non_integer_product_id(self) -> None:
        """Present official IDs must be positive non-boolean integers."""
        for invalid in (0, -1, True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                catalog = catalog_for([product("SPSF-1")])
                video_map(catalog)["SPSF-1"]["productId"] = invalid
                errors = validate_catalog(catalog)
                self.assertTrue(
                    any("productId must be a positive integer" in error for error in errors),
                    errors,
                )

    def test_rejects_duplicate_product_ids_deterministically(self) -> None:
        """One official product ID cannot be published under two catalog codes."""
        catalog = catalog_for(
            [
                product("B-1", productId=7),
                product("A-1", productId=7),
            ]
        )

        errors = validate_catalog(catalog)

        self.assertIn("duplicate productId 7: A-1, B-1", errors)

    def test_rejects_missing_or_malformed_refresh_release_metadata(self) -> None:
        """Deployable catalogs need an exact, internally consistent refresh contract."""
        cases = {
            "missing": lambda value: value.pop("refresh"),
            "not mapping": lambda value: value.update(refresh=[]),
            "bad mode": lambda value: value["refresh"].update(mode="tail"),
            "non-boolean completion": lambda value: value["refresh"].update(
                sourceComplete=1
            ),
            "missing count": lambda value: value["refresh"]["counts"].pop("added"),
            "extra count": lambda value: value["refresh"]["counts"].update(extra=0),
            "boolean count": lambda value: value["refresh"]["counts"].update(added=True),
            "negative count": lambda value: value["refresh"]["counts"].update(updated=-1),
            "current count mismatch": lambda value: value["refresh"]["counts"].update(
                retained=1
            ),
            "linked count mismatch": lambda value: value["refresh"]["counts"].update(
                linked=1
            ),
            "inputs not mapping": lambda value: value["refresh"].update(inputs=[]),
            "bad input digest": lambda value: value["refresh"].update(
                inputs={"productsSha256": "ABC"}
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                catalog = catalog_for([product("SPSF-1")])
                mutate(catalog)
                self.assertTrue(
                    any("refresh" in error for error in validate_catalog(catalog)),
                    validate_catalog(catalog),
                )

    def test_accepts_deterministic_optional_refresh_input_hashes(self) -> None:
        """Input provenance hashes are optional, but valid hashes are stable lowercase hex."""
        catalog = catalog_for([product("SPSF-1")])
        catalog["refresh"]["inputs"] = {
            "productsSha256": "a" * 64,
            "sheetSha256": "0" * 64,
        }

        self.assertEqual(validate_catalog(catalog), [])

    def test_enforces_default_release_floor_and_opt_in_strict_link_coverage(self) -> None:
        """The historical scope starts on 2007-12-07 and link coverage is optional."""
        before_floor = catalog_for([product("OLD-1", date="2007-12-06")])
        on_floor = catalog_for([product("OLD-1", date=DEFAULT_MIN_RELEASE_DATE)])
        unlinked = catalog_for([product("SPSF-1")])

        self.assertTrue(
            any("before minimum release date" in error for error in validate_catalog(before_floor))
        )
        self.assertEqual(validate_catalog(on_floor), [])
        self.assertEqual(validate_catalog(unlinked), [])
        self.assertTrue(
            any(
                "strict link coverage" in error
                for error in validate_catalog(
                    unlinked, refresh_context={"strictLinks": True}
                )
            )
        )


class RefreshModeValidationTests(unittest.TestCase):
    def test_rejects_requested_mode_mismatch_and_every_false_derived_count(self) -> None:
        """Public refresh metadata is recomputed from the two catalog generations."""
        previous = catalog_for(
            [
                product("A-1", productId=1),
                product("A-2", productId=2),
                product("A-3", productId=3),
                product("A-4", title="Old A-4", productId=4),
            ],
            {
                "A-1": {"gofile": "https://example.test/old-a1"},
                "A-2": {"gofile": "https://example.test/old-a2"},
            },
        )
        context = {
            "mode": "audit",
            "scanComplete": True,
            "linkConflicts": 3,
            "maxRegressionFraction": 1.0,
        }
        candidate = build_catalog(
            [
                product("A-1", productId=1),
                product("A-3", productId=3),
                product("A-4", title="New A-4", productId=4),
                product("A-5", productId=5),
            ],
            {
                "A-1": {"gofile": "https://example.test/new-a1"},
                "A-3": {"gofile": "https://example.test/new-a3"},
                "A-5": {"gofile": "https://example.test/new-a5"},
            },
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context=context,
        )[0]
        self.assertEqual(
            validate_catalog(
                candidate,
                previous,
                mode="audit",
                refresh_context=context,
            ),
            [],
        )

        mode_lie = copy.deepcopy(candidate)
        mode_lie["refresh"]["mode"] = "links-only"
        self.assertIn(
            "refresh.mode mismatch: expected audit",
            validate_catalog(
                mode_lie,
                previous,
                mode="audit",
                refresh_context=context,
            ),
        )

        completion_lie = copy.deepcopy(candidate)
        completion_lie["refresh"]["sourceComplete"] = False
        self.assertIn(
            "refresh.sourceComplete mismatch: expected true",
            validate_catalog(
                completion_lie,
                previous,
                mode="audit",
                refresh_context=context,
            ),
        )

        mutations = {
            "added": {"added": 2, "retained": 1},
            "updated": {"updated": 2, "retained": 1},
            "retained": {"retained": 3, "updated": 0},
            "deleted": {"deleted": 2},
            "linked": {"linked": 4},
            "linkAdded": {"linkAdded": 3},
            "linkUpdated": {"linkUpdated": 0},
            "linkRemoved": {"linkRemoved": 0},
            "linkConflicts": {"linkConflicts": 2},
        }
        for count_name, updates in mutations.items():
            with self.subTest(count_name=count_name):
                false_counts = copy.deepcopy(candidate)
                false_counts["refresh"]["counts"].update(updates)
                errors = validate_catalog(
                    false_counts,
                    previous,
                    mode="audit",
                    refresh_context=context,
                )
                self.assertTrue(
                    any(
                        error.startswith(
                            f"refresh.counts.{count_name} mismatch"
                        )
                        for error in errors
                    ),
                    errors,
                )

    def test_incremental_never_deletes_a_previous_product(self) -> None:
        """An incomplete daily scan cannot erase a previously published code."""
        previous = catalog_for(
            [product("SPSF-1", productId=1), product("SPSF-2", productId=2)]
        )
        current = build_catalog(
            [product("SPSF-2", productId=2), product("SPSF-3", productId=3)],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )[0]
        current["refresh"]["mode"] = "incremental"
        current["refresh"]["sourceComplete"] = False

        errors = validate_catalog(current, previous, mode="incremental")

        self.assertTrue(any("incremental removed SPSF-1" in error for error in errors), errors)

    def test_links_only_preserves_every_metadata_value_but_may_change_links(self) -> None:
        """Links-only must not use sheet refreshes as permission to rewrite metadata."""
        previous = catalog_for(
            [product("SPSF-1", productId=10)],
            {"SPSF-1": {"gofile": "https://example.test/old"}},
        )
        allowed = build_catalog(
            [product("SPSF-1", productId=10)],
            {"SPSF-1": {"gofile": "https://example.test/new"}},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "links-only"},
        )[0]
        changed = copy.deepcopy(allowed)
        video_map(changed)["SPSF-1"]["title"] = "Changed metadata"

        self.assertEqual(validate_catalog(allowed, previous, mode="links-only"), [])
        self.assertTrue(
            any(
                "links-only metadata changed" in error
                for error in validate_catalog(changed, previous, mode="links-only")
            )
        )

    def test_audit_deletion_requires_complete_scan_and_regression_gates(self) -> None:
        """A missing code is actionable only after a complete, acceptably small audit loss."""
        previous = catalog_for(
            [
                product(f"SPSF-{number}", productId=number)
                for number in range(1, 11)
            ]
        )
        current = build_catalog(
            [
                product(f"SPSF-{number}", productId=number)
                for number in range(2, 11)
            ],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )[0]

        incomplete = validate_catalog(
            current,
            previous,
            mode="audit",
            refresh_context={"scanComplete": False},
        )
        complete = validate_catalog(
            current,
            previous,
            mode="audit",
            refresh_context={"scanComplete": True},
        )

        self.assertTrue(any("audit scan is incomplete" in error for error in incomplete), incomplete)
        self.assertEqual(complete, [])

    def test_audit_checks_global_and_per_series_fifteen_percent_boundaries(self) -> None:
        """Offsetting additions must not hide a severe loss inside one series."""
        previous = catalog_for(
            [
                product(f"A-{number}", productId=number)
                for number in range(1, 21)
            ]
            + [
                product(f"B-{number}", productId=100 + number)
                for number in range(1, 21)
            ]
        )
        exact_boundary = build_catalog(
            [
                product(f"A-{number}", productId=number)
                for number in range(4, 21)
            ]
            + [
                product(f"B-{number}", productId=100 + number)
                for number in range(1, 21)
            ],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )[0]
        severe_series = build_catalog(
            [
                product(f"A-{number}", productId=number)
                for number in range(5, 21)
            ]
            + [
                product(f"B-{number}", productId=100 + number)
                for number in range(1, 25)
            ],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )[0]

        self.assertEqual(
            validate_catalog(
                exact_boundary,
                previous,
                mode="audit",
                refresh_context={"scanComplete": True},
            ),
            [],
        )
        errors = validate_catalog(
            severe_series,
            previous,
            mode="audit",
            refresh_context={"scanComplete": True},
        )
        self.assertTrue(any("series A regression" in error for error in errors), errors)

    def test_bounded_audit_cannot_delete_products_outside_requested_scope(self) -> None:
        """ID and date bounds narrow authority; they do not authorize global deletion."""
        previous = catalog_for(
            [
                product("SPSF-1", date="2020-01-01", productId=1),
                product("SPSF-2", date="2026-01-01", productId=2),
                product("SPSF-3", date="2026-01-01", productId=3),
            ]
        )
        missing_outside = build_catalog(
            [
                product("SPSF-2", date="2026-01-01", productId=2),
                product("SPSF-3", date="2026-01-01", productId=3),
            ],
            {},
            generated_at=GENERATED_AT,
            previous_catalog=previous,
            refresh_context={"mode": "audit", "scanComplete": True},
        )[0]

        errors = validate_catalog(
            missing_outside,
            previous,
            mode="audit",
            refresh_context={
                "scanComplete": True,
                "startId": 2,
                "endId": 3,
                "minReleaseDate": "2025-01-01",
            },
        )

        self.assertTrue(any("outside audit bounds" in error for error in errors), errors)

    def test_strict_links_rejects_import_conflicts_in_refresh_context(self) -> None:
        """Strict mode must promote sheet conflict diagnostics into a release gate."""
        catalog = build_catalog(
            [product("SPSF-1")],
            {"SPSF-1": {"gofile": "https://example.test/file"}},
            generated_at=GENERATED_AT,
            refresh_context={"mode": "incremental", "linkConflicts": 1},
        )[0]

        errors = validate_catalog(
            catalog,
            refresh_context={"strictLinks": True, "linkConflicts": 1},
        )

        self.assertTrue(any("strict link conflicts" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
