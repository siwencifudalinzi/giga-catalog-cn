import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.refresh import (
    DEFAULT_BASE_URL,
    DEFAULT_MIN_RELEASE_DATE,
    DEFAULT_SHEET_URL,
    DEFAULT_SUBTITLE_URL,
    RefreshError,
    _filter_products,
    create_parser,
    run_refresh as production_run_refresh,
)
from src.giga_catalog.merge import build_catalog, serialize_catalog
from src.giga_catalog.sheet import SheetFormatError
from tests.python.test_subtitles import subtitle_directory_xlsx


GENERATED_AT = "2026-07-29T00:00:00Z"
SHEET_HEADER = (
    "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK,UNCENSORED,"
    "STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
)
SUBTITLE_DIRECTORY_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/"
    "htmlview/sheet?pli=1&headers=true&gid=0"
)
SUBTITLE_DIRECTORY_HTML = (
    Path(__file__).parents[1] / "fixtures" / "subtitle_directory.html"
).read_text(encoding="utf-8")
EMPTY_COLLECTION_DIRECTORY_HTML = """<!doctype html>
<html><head><style>.waffle .black { color: #000000; }</style></head><body>
<table class="waffle"><tbody>
<tr><td class="black">NEW CODE</td><td class="black">STREAMTAPE LINK</td>
<td class="black">GOFILE LINK</td><td class="black">UNCENSORED</td></tr>
<tr><td class="black">BLUE NORMAL OR DOWN</td></tr>
<tr><td class="black">BLACK NOTHING</td></tr>
<tr><td class="black">RED NEWEST</td></tr>
<tr><td class="black">ORANGE LIST NOT COMPLETE</td></tr>
</tbody></table></body></html>"""
CURRENT_SUBTITLE_RESOURCES = {
    "subtitleDirectory": {
        "label": "SRT ENGSUB DOWNLOAD",
        "url": "https://ouo.io/BAbfv4",
    }
}
_DEFAULT_RESOURCES = object()


def fixture_subtitle_downloader(url, *, timeout, retries, delay_seconds):
    """Explicit no-network default for every refresh pipeline test."""
    return SUBTITLE_DIRECTORY_HTML


def fixture_collection_downloader(url, *, timeout, retries, delay_seconds):
    """Explicit no-network collection default with no current reupload sheets."""
    return EMPTY_COLLECTION_DIRECTORY_HTML


def run_refresh(*args, **kwargs):
    kwargs.setdefault("subtitle_downloader", fixture_subtitle_downloader)
    kwargs.setdefault("collection_downloader", fixture_collection_downloader)
    return production_run_refresh(*args, **kwargs)


def signed_subtitle_manifest(snapshot):
    snapshot = dict(snapshot)
    snapshot.pop("sha256", None)
    encoded = (
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return {**snapshot, "sha256": hashlib.sha256(encoded).hexdigest()}


def current_subtitle_manifest():
    return signed_subtitle_manifest({
        "schemaVersion": 1,
        "sourceUrl": SUBTITLE_DIRECTORY_URL,
        "legendColor": "#ff00ff",
        "portal": CURRENT_SUBTITLE_RESOURCES["subtitleDirectory"],
        "resolvedSources": [],
        "unresolvedSources": [
            {
                "series": "PGHD",
                "url": "https://ouo.io/2yaA66",
                "reason": "opaque_destination",
            }
        ],
        "childSources": [],
    })


def write_current_subtitle_state(data_root, state=None):
    value = dict(state or {"schemaVersion": 1})
    value["subtitle"] = current_subtitle_manifest()
    path = data_root / "state" / "scrape-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def product(code="SPSF-1", **overrides):
    value = {
        "productId": 1,
        "code": code,
        "title": "Title",
        "actors": ["Actor"],
        "releaseDate": "2026-07-01",
        "cover": "https://example.test/cover.jpg",
    }
    value.update(overrides)
    return value


def write_legacy(directory):
    directory.mkdir(parents=True)
    (directory / "data.json").write_text(
        json.dumps(
            [
                {
                    "code": "SPSF-1",
                    "title": "Legacy title",
                    "actors": ["Legacy actor"],
                    "date": "2026-07-01",
                    "cover": "https://example.test/legacy.jpg",
                }
            ]
        ),
        encoding="utf-8",
    )
    (directory / "links.json").write_text(
        json.dumps(
            {
                "SPSF-1": {
                    "st": "https://example.test/old",
                    "gf": "",
                }
            }
        ),
        encoding="utf-8",
    )


def write_previous(
    output_root,
    products=None,
    links=None,
    resources=_DEFAULT_RESOURCES,
):
    if resources is _DEFAULT_RESOURCES:
        resources = CURRENT_SUBTITLE_RESOURCES
    catalog = build_catalog(
        products or [product()],
        links or {"SPSF-1": {"gofile": "https://example.test/old"}},
        generated_at="2026-07-28T00:00:00Z",
        resources=resources,
    )[0]
    path = output_root / "data" / "catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_catalog(catalog))
    return catalog


def seed_publish_targets(root):
    output = root / "public"
    data = root / "private"
    write_previous(output)
    (output / "data" / "update-summary.json").write_bytes(b"old public summary")
    (data / "raw").mkdir(parents=True)
    (data / "state").mkdir(parents=True)
    (data / "raw" / "products.json").write_bytes(b"old products")
    (data / "raw" / "sheet.csv").write_bytes(b"old sheet")
    (data / "state" / "scrape-state.json").write_text(
        json.dumps({"schemaVersion": 1}), encoding="utf-8"
    )
    (data / "update-summary.json").write_bytes(b"old private summary")
    return output, data


def snapshot_tree(root):
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class RefreshArgumentTests(unittest.TestCase):
    def test_exposes_real_cli_flags_and_repository_rooted_defaults(self) -> None:
        """Missing or decorative refresh flags would make automation unreproducible."""
        options = create_parser().parse_args([])

        self.assertEqual(options.mode, "incremental")
        self.assertEqual(options.min_release_date, DEFAULT_MIN_RELEASE_DATE)
        self.assertEqual(options.sheet_url, DEFAULT_SHEET_URL)
        self.assertEqual(options.subtitle_url, DEFAULT_SUBTITLE_URL)
        self.assertEqual(options.base_url, DEFAULT_BASE_URL)
        self.assertFalse(options.strict_links)
        self.assertEqual(
            {
                action.dest
                for action in create_parser()._actions
                if action.dest != "help"
            },
            {
                "mode",
                "legacy_dir",
                "dry_run",
                "start_id",
                "end_id",
                "min_release_date",
                "sheet_url",
                "subtitle_url",
                "base_url",
                "output_root",
                "data_root",
                "timeout",
                "retries",
                "delay",
                "strict_links",
            },
        )

    def test_filters_old_source_variant_but_fails_closed_when_floor_includes_it(self) -> None:
        """Resolved legacy source evidence cannot leak or be silently dropped when in scope."""
        variant = {
            "productId": 2021,
            "code": "YNO-3B",
            "series": "YNO",
            "number": 3,
            "title": "Legacy letter variant",
            "actors": [],
            "releaseDate": "2004-05-22",
            "cover": "https://www.giga-web.jp/db_titles/yno/yno03b/pac_s.jpg",
        }
        default_options = create_parser().parse_args([])
        expanded_options = create_parser().parse_args(
            ["--min-release-date", "2000-01-01"]
        )

        self.assertEqual(_filter_products([variant], default_options), [])
        with self.assertRaisesRegex(
            RefreshError, "official product code is not publishable: YNO-3B"
        ):
            _filter_products([variant], expanded_options)

    def test_rejects_invalid_bounds_and_network_settings_before_io(self) -> None:
        """Bad ranges and retry settings must fail without touching sources or outputs."""
        cases = [
            ["--start-id", "10", "--end-id", "9"],
            ["--timeout", "0"],
            ["--retries", "0"],
            ["--delay", "-1"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(RefreshError):
                    run_refresh(
                        arguments,
                        sheet_downloader=lambda *args, **kwargs: self.fail(
                            "I/O occurred before argument validation"
                        ),
                    )

    def test_rejects_private_root_equal_to_or_nested_under_public_root_before_io(self) -> None:
        """Raw/state/audit artifacts must never be placeable below the deploy root."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            public_root = root / "public"
            for private_root in (public_root, public_root / "private"):
                with self.subTest(private_root=private_root):
                    with self.assertRaisesRegex(
                        RefreshError, "public and private paths must be disjoint"
                    ):
                        run_refresh(
                            [
                                "--output-root",
                                str(public_root),
                                "--data-root",
                                str(private_root),
                            ],
                            sheet_downloader=lambda *args, **kwargs: self.fail(
                                "sheet I/O occurred before root validation"
                            ),
                            discoverer=lambda *args, **kwargs: self.fail(
                                "GIGA I/O occurred before root validation"
                            ),
                        )
                    self.assertFalse(public_root.exists())

    def test_rejects_public_root_nested_under_private_root_before_io(self) -> None:
        """Reverse nesting can expose fixed raw/state paths inside the deploy tree."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            private_root = root / "private"
            for public_root in (private_root / "raw", private_root / "state"):
                with self.subTest(public_root=public_root):
                    with self.assertRaisesRegex(
                        RefreshError, "public and private paths must be disjoint"
                    ):
                        run_refresh(
                            [
                                "--output-root",
                                str(public_root),
                                "--data-root",
                                str(private_root),
                            ],
                            sheet_downloader=lambda *args, **kwargs: self.fail(
                                "sheet I/O occurred before reverse-root validation"
                            ),
                            discoverer=lambda *args, **kwargs: self.fail(
                                "GIGA I/O occurred before reverse-root validation"
                            ),
                        )
                    self.assertFalse(private_root.exists())

    def test_rejects_resolved_private_output_symlink_into_public_tree_before_io(self) -> None:
        """Disjoint roots are insufficient when a concrete private path resolves public."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            public_root = root / "public"
            private_root = root / "private"
            exposed_raw = public_root / "exposed-raw"
            exposed_raw.mkdir(parents=True)
            private_root.mkdir()
            try:
                (private_root / "raw").symlink_to(
                    exposed_raw,
                    target_is_directory=True,
                )
            except OSError as error:
                self.skipTest(f"directory symlinks unavailable: {error}")

            before = snapshot_tree(root)
            with self.assertRaisesRegex(
                RefreshError, "public and private paths must be disjoint"
            ):
                run_refresh(
                    [
                        "--output-root",
                        str(public_root),
                        "--data-root",
                        str(private_root),
                    ],
                    sheet_downloader=lambda *args, **kwargs: self.fail(
                        "sheet I/O occurred before concrete-path validation"
                    ),
                    discoverer=lambda *args, **kwargs: self.fail(
                        "GIGA I/O occurred before concrete-path validation"
                    ),
                )
            self.assertEqual(snapshot_tree(root), before)


class RefreshPipelineTests(unittest.TestCase):
    def test_available_collection_child_overlays_reupload_without_erasing_old_links(self) -> None:
        """A current blue sheet adds one external reupload slot and preserves providers."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(
                output,
                products=[product("AHEF-1")],
                links={"AHEF-1": {"gofile": "https://example.test/old"}},
            )
            directory = EMPTY_COLLECTION_DIRECTORY_HTML.replace(
                "</tbody>",
                '<tr><td style="color:#1155cc"><a href="https://docs.google.com/'
                'spreadsheets/d/ahef-child/edit?gid=0#gid=0">AHEF</a></td></tr>'
                "</tbody>",
            ).replace(
                "<style>.waffle .black { color: #000000; }</style>",
                "<style>.waffle .black { color: #000000; }"
                ".waffle .blue { color: #1155cc; }</style>",
            ).replace('style="color:#1155cc"', 'class="blue"')
            calls = []

            def collection_download(url, **kwargs):
                calls.append(url)
                if url == SUBTITLE_DIRECTORY_URL:
                    return directory
                return "AHEF-01,https://ouo.io/reuploaded\n"

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                collection_downloader=collection_download,
                featured_cover_refresher=lambda *args, **kwargs: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            catalog = json.loads(
                (output / "data" / "catalog.json").read_text(encoding="utf-8")
            )
            links = catalog["series"][0]["videos"][0]["links"]
            self.assertEqual(links["gofile"], "https://example.test/old")
            self.assertEqual(links["reupload"], "https://ouo.io/reuploaded")
            self.assertEqual(
                calls,
                [
                    SUBTITLE_DIRECTORY_URL,
                    "https://docs.google.com/spreadsheets/d/ahef-child/"
                    "export?format=csv&gid=0",
                ],
            )
            self.assertEqual(result["internal"]["sources"]["collection"]["childSheets"], 1)
            self.assertEqual(result["internal"]["sources"]["collection"]["linkKeys"], 1)

    def test_subtitle_policy_and_resource_only_change_publish_current_evidence(self) -> None:
        """The first portal import is content even when no per-video subtitle resolves."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output, products=[product("PGHD-1")], resources={})
            calls = []

            def download(url, *, timeout, retries, delay_seconds):
                calls.append((url, timeout, retries, delay_seconds))
                return subtitle_directory_xlsx()

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--timeout",
                    "9",
                    "--retries",
                    "5",
                    "--delay",
                    "0.125",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                subtitle_downloader=download,
                discoverer=lambda *args, **kwargs: self.fail(
                    "links-only contacted GIGA"
                ),
                featured_cover_refresher=lambda *args, **kwargs: {
                    "published": False
                },
                clock=lambda: GENERATED_AT,
            )

            catalog = json.loads(
                (output / "data" / "catalog.json").read_text(encoding="utf-8")
            )
            subtitle_evidence = result["internal"]["sources"]["subtitles"]

            self.assertTrue(result["changed"])
            self.assertEqual(
                calls,
                [(SUBTITLE_DIRECTORY_URL, 9.0, 5, 0.125)],
            )
            self.assertEqual(
                catalog["resources"],
                {
                    "subtitleDirectory": {
                        "label": "SRT ENGSUB DOWNLOAD",
                        "url": "https://ouo.io/BAbfv4",
                    }
                },
            )
            self.assertNotIn("links", catalog["series"][0])
            self.assertNotIn(
                "subtitle", catalog["series"][0]["videos"][0].get("links", {})
            )
            self.assertEqual(subtitle_evidence["portalCount"], 1)
            self.assertEqual(subtitle_evidence["unresolvedCount"], 1)
            self.assertEqual(subtitle_evidence["videoLinks"], 0)
            self.assertTrue((data / "raw" / "subtitles.json").is_file())
            state = json.loads(
                (data / "state" / "scrape-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["subtitle"]["sha256"], subtitle_evidence["sha256"]
            )

    def test_subtitle_canonical_hash_ignores_google_redirect_expiry_parameters(self) -> None:
        """Volatile Google wrapper parameters cannot manufacture a daily input change."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output, products=[product("PGHD-1")])

            hashes = []
            for html in (
                SUBTITLE_DIRECTORY_HTML,
                SUBTITLE_DIRECTORY_HTML.replace("111111", "999999").replace(
                    "usg=volatile", "usg=different"
                ),
            ):
                result = run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--dry-run",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    subtitle_downloader=lambda *args, html=html, **kwargs: html,
                    clock=lambda: GENERATED_AT,
                )
                hashes.append(
                    result["internal"]["sources"]["subtitles"]["sha256"]
                )

            self.assertEqual(hashes[0], hashes[1])

    def test_subtitle_main_or_pink_child_failure_writes_nothing(self) -> None:
        """Every required subtitle fetch completes before the transaction mutates files."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, data = seed_publish_targets(root)
            write_previous(
                output,
                products=[
                    product("PGHD-1", productId=1),
                    product("SPSF-44", productId=44),
                    product("SPSF-45", productId=45),
                ],
            )
            before = snapshot_tree(root)

            def main_failure(*args, **kwargs):
                raise OSError("subtitle main failed")

            with self.assertRaisesRegex(OSError, "subtitle main failed"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    subtitle_downloader=main_failure,
                    clock=lambda: GENERATED_AT,
                )
            self.assertEqual(snapshot_tree(root), before)

            child_html = SUBTITLE_DIRECTORY_HTML.replace(
                '<td class="s_red">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdocs.google.com%2Fspreadsheets%2Fd%2Fchild-spsf',
                '<td class="s_pink">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdocs.google.com%2Fspreadsheets%2Fd%2Fchild-spsf',
                1,
            )
            child_calls = []

            def child_failure(url, **kwargs):
                child_calls.append(url)
                if url == SUBTITLE_DIRECTORY_URL:
                    return child_html
                raise OSError("subtitle child failed")

            with self.assertRaisesRegex(OSError, "subtitle child failed"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    subtitle_downloader=child_failure,
                    clock=lambda: GENERATED_AT,
                )
            self.assertEqual(
                child_calls,
                [
                    SUBTITLE_DIRECTORY_URL,
                    "https://docs.google.com/spreadsheets/d/child-spsf/"
                    "export?format=csv&gid=128",
                ],
            )
            self.assertEqual(snapshot_tree(root), before)

    def test_subtitle_evidence_only_change_is_a_publication_reason(self) -> None:
        """A new canonical private observation must not be discarded as catalog-noop."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            resources = {
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            }
            write_previous(
                output,
                products=[product("PGHD-1")],
                resources=resources,
            )
            (data / "state").mkdir(parents=True)
            (data / "state" / "scrape-state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "subtitle": signed_subtitle_manifest({
                            "schemaVersion": 1,
                            "sourceUrl": SUBTITLE_DIRECTORY_URL,
                            "legendColor": "#ff00ff",
                            "portal": resources["subtitleDirectory"],
                            "resolvedSources": [],
                            "unresolvedSources": [
                                {
                                    "series": "PGHD",
                                    "url": "https://ouo.io/previous",
                                    "reason": "opaque_destination",
                                }
                            ],
                            "childSources": [],
                        }),
                    }
                ),
                encoding="utf-8",
            )

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                clock=lambda: GENERATED_AT,
                featured_cover_refresher=lambda *args, **kwargs: {
                    "published": False
                },
            )

            state = json.loads(
                (data / "state" / "scrape-state.json").read_text(encoding="utf-8")
            )
            self.assertTrue(result["changed"])
            self.assertEqual(state["subtitle"], current_subtitle_manifest())
            self.assertTrue((data / "raw" / "subtitles.json").is_file())

    def test_previous_resolved_subtitle_degradation_fails_before_writes(self) -> None:
        """A once-resolved pink source cannot silently become missing or opaque."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, data = seed_publish_targets(root)
            resources = {
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/BAbfv4",
                }
            }
            write_previous(
                output,
                products=[product("PGHD-1")],
                resources=resources,
            )
            state_path = data / "state" / "scrape-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "subtitle": signed_subtitle_manifest({
                            "schemaVersion": 1,
                            "sourceUrl": SUBTITLE_DIRECTORY_URL,
                            "legendColor": "#ff00ff",
                            "portal": resources["subtitleDirectory"],
                            "resolvedSources": [
                                {
                                    "scope": "series",
                                    "series": "PGHD",
                                    "url": "https://drive.google.com/open?id=verified",
                                }
                            ],
                            "unresolvedSources": [],
                            "childSources": [],
                        }),
                    }
                ),
                encoding="utf-8",
            )
            before = snapshot_tree(root)

            with self.assertRaisesRegex(
                RefreshError, "resolved subtitle source disappeared or changed"
            ):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)

    def test_corrupt_or_forged_subtitle_state_fails_before_source_io(self) -> None:
        """An existing invalid manifest cannot be reinterpreted as a first synchronization."""
        forged = current_subtitle_manifest()
        forged["sha256"] = "0" * 64
        cases = (
            "{not json",
            "[]",
            json.dumps({"schemaVersion": 1, "subtitle": forged}),
        )

        for state_text in cases:
            with self.subTest(state_text=state_text), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                output, data = seed_publish_targets(root)
                state_path = data / "state" / "scrape-state.json"
                state_path.write_text(state_text, encoding="utf-8")
                before = snapshot_tree(root)

                with self.assertRaises(RefreshError):
                    run_refresh(
                        [
                            "--mode",
                            "links-only",
                            "--output-root",
                            str(output),
                            "--data-root",
                            str(data),
                        ],
                        sheet_downloader=lambda *args, **kwargs: self.fail(
                            "sheet I/O occurred after invalid subtitle state"
                        ),
                        subtitle_downloader=lambda *args, **kwargs: self.fail(
                            "subtitle I/O occurred after invalid subtitle state"
                        ),
                        clock=lambda: GENERATED_AT,
                    )

                self.assertEqual(snapshot_tree(root), before)

    def test_self_hashed_incomplete_subtitle_state_fails_before_all_io_and_writes(self) -> None:
        """A matching digest cannot authorize a structurally incomplete manifest."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output, data = seed_publish_targets(root)
            (data / "raw" / "subtitles.json").write_bytes(b"old subtitles")
            state_path = data / "state" / "scrape-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "subtitle": signed_subtitle_manifest(
                            {
                                "legendColor": "#ff00ff",
                                "resolvedSources": [],
                            }
                        ),
                    }
                ),
                encoding="utf-8",
            )
            before = snapshot_tree(root)
            calls = []

            def source(name, result):
                def download(*args, **kwargs):
                    calls.append(name)
                    return result

                return download

            discovery = (
                [],
                {
                    "mode": "incremental",
                    "pagesFetched": 2,
                    "parsedProducts": 1,
                    "newProducts": 0,
                    "knownProducts": 1,
                    "cursor": 2,
                    "retries": 0,
                    "errors": 0,
                    "stopReason": "all_known",
                    "authoritativeComplete": False,
                },
            )

            with self.assertRaisesRegex(RefreshError, "subtitle state"):
                run_refresh(
                    [
                        "--mode",
                        "incremental",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=source("sheet", SHEET_HEADER),
                    subtitle_downloader=source(
                        "subtitle",
                        SUBTITLE_DIRECTORY_HTML,
                    ),
                    discoverer=source("products", discovery),
                    featured_cover_refresher=lambda *args: {"published": False},
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(calls, [])
            self.assertEqual(len(before), 7)
            self.assertEqual(snapshot_tree(root), before)

    def test_explicit_global_portal_change_updates_and_publishes(self) -> None:
        """A valid URL under the unchanged portal label is authoritative, not append-only."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            old_resources = {
                "subtitleDirectory": {
                    "label": "SRT ENGSUB DOWNLOAD",
                    "url": "https://ouo.io/previous",
                }
            }
            write_previous(
                output,
                products=[product("PGHD-1")],
                resources=old_resources,
            )
            (data / "state").mkdir(parents=True)
            (data / "state" / "scrape-state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "subtitle": signed_subtitle_manifest({
                            "schemaVersion": 1,
                            "sourceUrl": SUBTITLE_DIRECTORY_URL,
                            "legendColor": "#ff00ff",
                            "portal": old_resources["subtitleDirectory"],
                            "resolvedSources": [],
                            "unresolvedSources": [],
                            "childSources": [],
                        }),
                    }
                ),
                encoding="utf-8",
            )

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                clock=lambda: GENERATED_AT,
                featured_cover_refresher=lambda *args, **kwargs: {
                    "published": False
                },
            )

            catalog = json.loads(
                (output / "data" / "catalog.json").read_text(encoding="utf-8")
            )
            self.assertTrue(result["changed"])
            self.assertEqual(
                catalog["resources"]["subtitleDirectory"]["url"],
                "https://ouo.io/BAbfv4",
            )

    def test_sheet_downloader_receives_cli_retry_and_delay_policy(self) -> None:
        """Sheet retry flags must reach the network boundary instead of only official discovery."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output)
            calls = []

            def download(url, *, timeout, retries, delay_seconds):
                calls.append((url, timeout, retries, delay_seconds))
                return SHEET_HEADER

            run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--dry-run",
                    "--sheet-url",
                    "https://sheet.test/export.csv",
                    "--timeout",
                    "9",
                    "--retries",
                    "5",
                    "--delay",
                    "0.125",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=download,
                discoverer=lambda *args, **kwargs: self.fail("links-only contacted GIGA"),
                clock=lambda: GENERATED_AT,
            )

        self.assertEqual(
            calls,
            [("https://sheet.test/export.csv", 9.0, 5, 0.125)],
        )

    def test_sheet_downloader_type_error_is_not_retried_with_a_smaller_signature(self) -> None:
        """A TypeError inside a compatible double must surface rather than masquerade as compatibility."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output)
            calls = 0

            def download(url, *, timeout, retries, delay_seconds):
                nonlocal calls
                calls += 1
                raise TypeError("double implementation failed")

            with self.assertRaisesRegex(TypeError, "double implementation failed"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--dry-run",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=download,
                    clock=lambda: GENERATED_AT,
                )

        self.assertEqual(calls, 1)

    def test_card_integrity_failure_never_uses_tail_or_changes_artifacts(self) -> None:
        """Tail additions cannot repair an unidentifiable or duplicate official directory card."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            before = snapshot_tree(root)
            modes = []

            def discover(existing, mode="incremental", **kwargs):
                modes.append(mode)
                if mode == "tail":
                    self.fail("deterministic card-integrity failure used tail fallback")
                return [], {
                    "mode": mode,
                    "pagesFetched": 1,
                    "parsedProducts": 0,
                    "newProducts": 0,
                    "knownProducts": 0,
                    "cursor": 1,
                    "retries": 0,
                    "errors": 1,
                    "stopReason": "error",
                    "error": "unresolved_directory_cards",
                    "cardIntegrityComplete": False,
                    "diagnostics": [
                        {"type": "unidentifiable_cards", "page": 1, "count": 1}
                    ],
                }

            for requested_mode in ("audit", "incremental"):
                modes.clear()
                with self.subTest(mode=requested_mode), self.assertRaisesRegex(
                    RefreshError,
                    "official directory card integrity.*unresolved_directory_cards",
                ):
                    run_refresh(
                        [
                            "--mode",
                            requested_mode,
                            "--output-root",
                            str(output),
                            "--data-root",
                            str(data),
                        ],
                        sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                        discoverer=discover,
                        featured_cover_refresher=lambda *args: self.fail(
                            "cover refresh ran after integrity failure"
                        ),
                        clock=lambda: GENERATED_AT,
                    )

                self.assertEqual(modes, [requested_mode])
                self.assertEqual(snapshot_tree(root), before)

    def test_directory_exception_never_uses_tail_or_changes_artifacts(self) -> None:
        """A decode/parser crash is not a structured network summary eligible for recovery."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            before = snapshot_tree(root)
            modes = []

            def discover(existing, mode="incremental", **kwargs):
                modes.append(mode)
                raise UnicodeDecodeError(
                    "utf-8", b"\xff", 0, 1, "invalid source encoding"
                )

            for requested_mode in ("incremental", "audit"):
                modes.clear()
                with self.subTest(mode=requested_mode), self.assertRaisesRegex(
                    RefreshError,
                    "official directory discovery failed: UnicodeDecodeError",
                ):
                    run_refresh(
                        [
                            "--mode",
                            requested_mode,
                            "--output-root",
                            str(output),
                            "--data-root",
                            str(data),
                        ],
                        sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                        discoverer=discover,
                        featured_cover_refresher=lambda *args: self.fail(
                            "cover refresh ran after directory exception"
                        ),
                        clock=lambda: GENERATED_AT,
                    )

                self.assertEqual(modes, [requested_mode])
                self.assertEqual(snapshot_tree(root), before)

    def test_invalid_directory_result_never_uses_tail_or_changes_artifacts(self) -> None:
        """A non-structured return cannot be rewritten into a fallback-eligible summary."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            before = snapshot_tree(root)
            modes = []

            def discover(existing, mode="incremental", **kwargs):
                modes.append(mode)
                return None

            with self.assertRaisesRegex(
                RefreshError, "official directory discovery failed: invalid result"
            ):
                run_refresh(
                    [
                        "--mode",
                        "incremental",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    discoverer=discover,
                    featured_cover_refresher=lambda *args: self.fail(
                        "cover refresh ran after invalid directory result"
                    ),
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(modes, ["incremental"])
            self.assertEqual(snapshot_tree(root), before)

    def test_unchanged_catalog_still_retries_the_independent_featured_cover_artifact(self) -> None:
        """A missing/corrupt cover cache must heal even when catalog bytes are unchanged."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            private = root / "private"
            write_previous(output)
            write_current_subtitle_state(private)
            calls = []

            result = run_refresh(
                ["--mode", "links-only", "--output-root", str(output), "--data-root", str(private)],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=lambda *args, **kwargs: self.fail("links-only contacted GIGA"),
                featured_cover_refresher=lambda *paths: calls.append(paths) or {"published": True},
                clock=lambda: GENERATED_AT,
            )

            self.assertFalse(result["changed"])
            self.assertEqual(result["featuredCovers"], {"published": True})
            self.assertEqual(len(calls), 1)

    def test_featured_cover_refresh_runs_after_publication_without_blocking_catalog_sync(self) -> None:
        """A cold/failed cover CDN must not roll back an otherwise valid catalog sync."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            private = root / "private"
            write_previous(output)
            calls = []

            def failed_cover_refresh(catalog_path, output_dir, manifest_path):
                calls.append((catalog_path, output_dir, manifest_path))
                raise OSError("cover origin unavailable")

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(private),
                ],
                sheet_downloader=lambda *args, **kwargs: (
                    SHEET_HEADER + "SPSF-1,,,https://example.test/updated,,,,\n"
                ),
                discoverer=lambda *args, **kwargs: self.fail("links-only contacted GIGA"),
                featured_cover_refresher=failed_cover_refresh,
                clock=lambda: GENERATED_AT,
            )

            self.assertTrue(result["changed"])
            self.assertEqual(result["featuredCovers"], {"published": False, "error": "cover origin unavailable"})
            self.assertEqual(
                [tuple(path.resolve() for path in call) for call in calls],
                [
                    (
                        (output / "data" / "catalog.json").resolve(),
                        (output / "media" / "featured-covers").resolve(),
                        (output / "data" / "featured-covers.json").resolve(),
                    )
                ],
            )
            published = json.loads((output / "data" / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(published["series"][0]["videos"][0]["links"]["gofile"], "https://example.test/updated")

    def test_invalid_previous_refresh_fails_before_sheet_or_official_io(self) -> None:
        """A malformed deployable baseline cannot be trusted as refresh state."""
        mutations = {
            "missing": lambda catalog: catalog.pop("refresh"),
            "malformed": lambda catalog: catalog["refresh"].update(counts={"added": 0}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory).resolve()
                output = root / "public"
                previous = write_previous(output)
                mutate(previous)
                (output / "data" / "catalog.json").write_text(
                    json.dumps(previous),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(RefreshError, "previous catalog is invalid"):
                    run_refresh(
                        [
                            "--mode",
                            "links-only",
                            "--output-root",
                            str(output),
                            "--data-root",
                            str(root / "private"),
                        ],
                        sheet_downloader=lambda *args, **kwargs: self.fail(
                            "sheet I/O occurred before previous-catalog validation"
                        ),
                        discoverer=lambda *args, **kwargs: self.fail(
                            "GIGA I/O occurred before previous-catalog validation"
                        ),
                    )

    def test_changed_sheet_schema_preserves_every_published_artifact(self) -> None:
        """An HTML/login response must fail closed before discovery or publication."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, private = seed_publish_targets(root)
            before = snapshot_tree(root)

            with self.assertRaisesRegex(
                SheetFormatError,
                "exactly one 'NEW CODE'",
            ):
                run_refresh(
                    [
                        "--mode",
                        "incremental",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(private),
                    ],
                    sheet_downloader=lambda *args, **kwargs: "<html>login</html>",
                    discoverer=lambda *args, **kwargs: self.fail(
                        "GIGA I/O occurred after malformed sheet"
                    ),
                    featured_cover_refresher=lambda *args, **kwargs: self.fail(
                        "cover publication occurred after malformed sheet"
                    ),
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_loads_a_valid_historical_refresh_without_reinterpreting_it_as_bootstrap(self) -> None:
        """Prior diff counts describe an unavailable generation, not an empty catalog."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            older = write_previous(output)
            historical = build_catalog(
                [product()],
                {"SPSF-1": {"gofile": "https://example.test/historical"}},
                generated_at="2026-07-28T12:00:00Z",
                previous_catalog=older,
                refresh_context={"mode": "links-only", "scanComplete": False},
            )[0]
            (output / "data" / "catalog.json").write_bytes(
                serialize_catalog(historical)
            )

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(root / "private"),
                    "--dry-run",
                ],
                sheet_downloader=lambda *args, **kwargs: (
                    SHEET_HEADER
                    + "SPSF-1,,,https://example.test/historical,,,,\n"
                ),
                discoverer=lambda *args, **kwargs: self.fail(
                    "links-only contacted GIGA"
                ),
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 1)

    def test_links_only_never_contacts_giga_and_seeds_missing_catalog_from_legacy(self) -> None:
        """A first links-only refresh must bootstrap locally without a GIGA request."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            legacy = root / "legacy"
            output = root / "public"
            data = root / "private"
            write_legacy(legacy)

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--legacy-dir",
                    str(legacy),
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda url, timeout=0, retries=3, delay_seconds=1: (
                    SHEET_HEADER
                    + "SPSF-1,https://example.test/new,,,,,,\n"
                ),
                discoverer=lambda *args, **kwargs: self.fail(
                    "links-only contacted GIGA"
                ),
                clock=lambda: GENERATED_AT,
            )

            catalog = json.loads((output / "data" / "catalog.json").read_text("utf-8"))
            self.assertEqual(catalog["totals"]["videos"], 1)
            self.assertEqual(
                catalog["series"][0]["videos"][0]["links"],
                {"streamtape": "https://example.test/new"},
            )
            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 1)
            self.assertFalse(result["publicRefresh"]["sourceComplete"])
            self.assertTrue((data / "update-summary.json").is_file())
            self.assertFalse((output / "data" / "update-summary.json").exists())

    def test_links_only_preserves_all_previous_metadata_logical_values(self) -> None:
        """Replacing links must leave every existing video metadata value untouched."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            previous = write_previous(
                output,
                [
                    product(
                        title="Exact title",
                        actors=["A", "B"],
                        cover=(
                            "https://www.giga-web.jp/db_titles/spsf/"
                            "spsf0001/pac_s.jpg"
                        ),
                        previewBase=(
                            "https://www.giga-web.jp/db_titles/spsf/"
                            "spsf0001/sample/"
                        ),
                        previewCount=18,
                    )
                ],
            )

            run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--legacy-dir",
                    str(root / "unused"),
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: (
                    SHEET_HEADER
                    + "SPSF-1,,,https://example.test/new,,,,\n"
                ),
                discoverer=lambda *args, **kwargs: self.fail(
                    "links-only contacted GIGA"
                ),
                clock=lambda: GENERATED_AT,
            )

            current = json.loads((output / "data" / "catalog.json").read_text("utf-8"))
            old_video = previous["series"][0]["videos"][0]
            new_video = current["series"][0]["videos"][0]
            self.assertEqual(
                {key: value for key, value in new_video.items() if key != "links"},
                {key: value for key, value in old_video.items() if key != "links"},
            )
            self.assertNotEqual(new_video["links"], old_video["links"])

    def test_sheet_overlay_preserves_prior_links_for_omitted_codes(self) -> None:
        """A sparse latest-code sheet must not erase the historical link catalog."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(
                output,
                [product("SPSF-1", productId=1), product("SPSF-2", productId=2)],
                {
                    "SPSF-1": {"gofile": "https://example.test/old-1"},
                    "SPSF-2": {"streamtape": "https://example.test/keep-2"},
                },
            )

            run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--legacy-dir",
                    str(root / "unused"),
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: (
                    SHEET_HEADER
                    + "SPSF-1,,,https://example.test/new-1,,,,\n"
                ),
                discoverer=lambda *args, **kwargs: self.fail(
                    "links-only contacted GIGA"
                ),
                clock=lambda: GENERATED_AT,
            )

            current = json.loads((output / "data" / "catalog.json").read_text("utf-8"))
            links = {
                video["code"]: video.get("links")
                for series in current["series"]
                for video in series["videos"]
            }
            self.assertEqual(
                links["SPSF-1"],
                {
                    "gofile": "https://example.test/new-1",
                },
            )
            self.assertEqual(
                links["SPSF-2"],
                {"streamtape": "https://example.test/keep-2"},
            )
            raw_products = json.loads(
                (data / "raw" / "products.json").read_text("utf-8")
            )
            self.assertEqual(
                [record["code"] for record in raw_products],
                ["SPSF-1", "SPSF-2"],
            )

    def test_audit_directory_error_uses_bounded_tail_fallback_without_deletion(self) -> None:
        """Recovered tail records may update an audit, but cannot authorize removals."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            write_previous(output, [product("SPSF-1", productId=1)])
            calls = []

            def discover(existing, mode="incremental", **kwargs):
                calls.append((mode, dict(kwargs)))
                if mode == "audit":
                    return [product("SPSF-2", productId=2)], {
                        "mode": "audit",
                        "pagesFetched": 2,
                        "parsedProducts": 1,
                        "newProducts": 1,
                        "knownProducts": 0,
                        "cursor": 2,
                        "retries": 3,
                        "errors": 1,
                        "stopReason": "error",
                        "error": "network_retries_exhausted",
                    }
                self.assertEqual(mode, "tail")
                return [product("SPSF-3", productId=3)], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 1,
                    "newProducts": 1,
                    "retries": 2,
                    "errors": 0,
                    "tailProbes": 4,
                    "tailMisses": 3,
                    "stopReason": "three_misses",
                }

            result = run_refresh(
                [
                    "--mode",
                    "audit",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(root / "private"),
                    "--base-url",
                    "https://catalog.example.test",
                    "--timeout",
                    "17",
                    "--retries",
                    "4",
                    "--delay",
                    "0.25",
                    "--dry-run",
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual([mode for mode, _ in calls], ["audit", "tail"])
            tail_kwargs = calls[1][1]
            self.assertGreater(tail_kwargs["page_limit"], 0)
            self.assertLessEqual(tail_kwargs["page_limit"], 100)
            self.assertEqual(tail_kwargs["base_url"], "https://catalog.example.test")
            self.assertEqual(tail_kwargs["timeout"], 17)
            self.assertEqual(tail_kwargs["retries"], 4)
            self.assertEqual(tail_kwargs["delay_seconds"], 0.25)
            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 3)
            self.assertFalse(result["publicRefresh"]["sourceComplete"])
            official = result["internal"]["sources"]["official"]
            self.assertTrue(official["fallbackUsed"])
            self.assertEqual(official["directory"]["stopReason"], "error")
            self.assertEqual(official["fallback"]["retries"], 2)

    def test_empty_first_directory_page_with_prior_catalog_uses_tail_fallback(self) -> None:
        """A structurally empty first page is unusable when a baseline already exists."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            write_previous(output)
            modes = []

            def discover(existing, mode="incremental", **kwargs):
                modes.append(mode)
                if mode == "incremental":
                    return [], {
                        "mode": mode,
                        "pagesFetched": 1,
                        "parsedProducts": 0,
                        "newProducts": 0,
                        "retries": 0,
                        "errors": 0,
                        "stopReason": "empty",
                    }
                return [], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 0,
                    "newProducts": 0,
                    "retries": 0,
                    "errors": 0,
                    "tailProbes": 3,
                    "tailMisses": 3,
                    "stopReason": "three_misses",
                }

            result = run_refresh(
                [
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(root / "private"),
                    "--dry-run",
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual(modes, ["incremental", "tail"])
            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 1)

    def test_incomplete_audit_summary_cannot_authorize_ten_percent_deletion(self) -> None:
        """Missing page/record/cursor evidence cannot make a partial audit complete."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            write_previous(
                output,
                [
                    product(f"SPSF-{number}", productId=number)
                    for number in range(1, 11)
                ],
            )
            calls = []

            def discover(existing, mode="incremental", **kwargs):
                calls.append(mode)
                if mode == "audit":
                    return [
                        product(f"SPSF-{number}", productId=number)
                        for number in range(1, 10)
                    ], {
                        "mode": "audit",
                        "errors": 0,
                        "stopReason": "empty",
                    }
                return [], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 0,
                    "newProducts": 0,
                    "knownProducts": 0,
                    "cursor": 13,
                    "retries": 0,
                    "errors": 0,
                    "tailProbes": 3,
                    "tailMisses": 3,
                    "stopReason": "three_misses",
                }

            result = run_refresh(
                [
                    "--mode",
                    "audit",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(root / "private"),
                    "--dry-run",
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual(calls, ["audit", "tail"])
            self.assertFalse(result["publicRefresh"]["sourceComplete"])
            self.assertEqual(result["publicRefresh"]["counts"]["deleted"], 0)
            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 10)

    def test_legacy_bootstrap_empty_first_page_uses_tail_fallback(self) -> None:
        """A nonempty legacy seed is a baseline even before catalog.json exists."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            legacy = root / "legacy"
            write_legacy(legacy)
            calls = []

            def discover(existing, mode="incremental", **kwargs):
                calls.append(mode)
                if mode == "incremental":
                    return [], {
                        "mode": "incremental",
                        "pagesFetched": 1,
                        "parsedProducts": 0,
                        "newProducts": 0,
                        "knownProducts": 0,
                        "cursor": 1,
                        "retries": 0,
                        "errors": 0,
                        "stopReason": "empty",
                    }
                return [product("SPSF-2", productId=2)], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 1,
                    "newProducts": 1,
                    "knownProducts": 0,
                    "cursor": 5,
                    "retries": 0,
                    "errors": 0,
                    "tailProbes": 4,
                    "tailMisses": 3,
                    "stopReason": "three_misses",
                }

            result = run_refresh(
                [
                    "--legacy-dir",
                    str(legacy),
                    "--output-root",
                    str(root / "public"),
                    "--data-root",
                    str(root / "private"),
                    "--dry-run",
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual(calls, ["incremental", "tail"])
            self.assertEqual(result["internal"]["counts"]["publishedProducts"], 2)

    def test_tail_fallback_retry_exhaustion_is_an_explicit_pre_publish_error(self) -> None:
        """A failed recovery probe must not be mistaken for a valid partial refresh."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            write_previous(output)
            before = snapshot_tree(root)

            def discover(existing, mode="incremental", **kwargs):
                if mode == "incremental":
                    return [], {
                        "mode": mode,
                        "pagesFetched": 0,
                        "parsedProducts": 0,
                        "newProducts": 0,
                        "retries": 3,
                        "errors": 1,
                        "stopReason": "error",
                    }
                return [], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 0,
                    "newProducts": 0,
                    "retries": 4,
                    "errors": 1,
                    "tailProbes": 1,
                    "tailMisses": 0,
                    "stopReason": "page_limit",
                    "error": "network_retries_exhausted",
                }

            with self.assertRaisesRegex(
                RefreshError, "tail fallback failed.*network_retries_exhausted"
            ):
                run_refresh(
                    [
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(root / "private"),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    discoverer=discover,
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)

    def test_every_mode_dry_run_performs_full_logic_with_zero_writes(self) -> None:
        """Dry-run must not create directories, state, raw data, summaries, or temp files."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = root / "legacy"
            write_legacy(legacy)
            before = snapshot_tree(root)

            def discover(existing, mode="incremental", **kwargs):
                if mode == "audit":
                    return list(existing), {
                        "mode": mode,
                        "errors": 0,
                        "stopReason": "empty",
                    }
                return [], {
                    "mode": mode,
                    "errors": 0,
                    "stopReason": "all_known",
                }

            for mode in ("incremental", "audit", "links-only"):
                result = run_refresh(
                    [
                        "--mode",
                        mode,
                        "--legacy-dir",
                        str(legacy),
                        "--output-root",
                        str(root / f"public-{mode}"),
                        "--data-root",
                        str(root / f"private-{mode}"),
                        "--dry-run",
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    discoverer=discover,
                    clock=lambda: GENERATED_AT,
                )
                self.assertTrue(result["dryRun"])
                self.assertEqual(result["internal"]["counts"]["publishedProducts"], 1)

            self.assertEqual(snapshot_tree(root), before)
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_validation_failure_occurs_before_any_filesystem_write(self) -> None:
        """The complete candidate must be gated in memory before mkdir or staging."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = root / "legacy"
            output = root / "new-public"
            data = root / "new-private"
            write_legacy(legacy)

            def reject(*args, **kwargs):
                self.assertFalse(output.exists())
                self.assertFalse(data.exists())
                return ["forced validation failure"]

            with self.assertRaisesRegex(RefreshError, "forced validation failure"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--legacy-dir",
                        str(legacy),
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    validator=reject,
                    clock=lambda: GENERATED_AT,
                )

            self.assertFalse(output.exists())
            self.assertFalse(data.exists())

    def test_replace_failure_preserves_every_existing_artifact_and_cleans_temp(self) -> None:
        """A failed atomic publish must leave the live catalog and private state untouched."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (data / "raw").mkdir(parents=True)
            (data / "state").mkdir(parents=True)
            (data / "raw" / "products.json").write_bytes(b"old products")
            (data / "raw" / "sheet.csv").write_bytes(b"old sheet")
            (data / "state" / "scrape-state.json").write_text(
                json.dumps({"schemaVersion": 1}), encoding="utf-8"
            )
            (data / "update-summary.json").write_bytes(b"old summary")
            before = snapshot_tree(root)

            def fail_replace(source, target):
                raise OSError("replace failed")

            with self.assertRaisesRegex(OSError, "replace failed"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--legacy-dir",
                        str(root / "unused"),
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: (
                        SHEET_HEADER
                        + "SPSF-1,,,https://example.test/new,,,,\n"
                    ),
                    replacer=fail_replace,
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)
            self.assertEqual(list(root.rglob("*.tmp")), [])

    def test_transaction_rolls_back_stale_cleanup_and_every_replacement(self) -> None:
        """Every injected live-mutation failure restores all six target states."""
        replacement_targets = [
            "public/data/catalog.json",
            "private/raw/products.json",
            "private/raw/sheet.csv",
            "private/state/scrape-state.json",
            "private/update-summary.json",
        ]
        failure_points = ["stale-cleanup"] + replacement_targets
        for failure_point in failure_points:
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory).resolve()
                output, data = seed_publish_targets(root)
                before = snapshot_tree(root)

                def replace(source, target):
                    relative = target.relative_to(root).as_posix()
                    if failure_point == relative:
                        raise OSError(f"injected {relative}")
                    source.replace(target)

                def remove_stale(path):
                    if failure_point == "stale-cleanup":
                        raise OSError("injected stale-cleanup")
                    path.unlink()

                with self.assertRaisesRegex(OSError, "injected"):
                    run_refresh(
                        [
                            "--mode",
                            "links-only",
                            "--output-root",
                            str(output),
                            "--data-root",
                            str(data),
                        ],
                        sheet_downloader=lambda *args, **kwargs: (
                            SHEET_HEADER
                            + "SPSF-1,,,https://example.test/new,,,,\n"
                        ),
                        replacer=replace,
                        stale_remover=remove_stale,
                        clock=lambda: GENERATED_AT,
                    )

                self.assertEqual(snapshot_tree(root), before)
                leftovers = [
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                    and (
                        ".refresh." in path.name
                        or path.suffix in {".tmp", ".bak", ".rollback"}
                    )
                ]
                self.assertEqual(leftovers, [])

    def test_all_targets_are_staged_and_fsynced_before_first_live_mutation(self) -> None:
        """The transaction has complete replacement and rollback material up front."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            live_targets = [
                output / "data" / "catalog.json",
                output / "data" / "update-summary.json",
                data / "raw" / "products.json",
                data / "raw" / "sheet.csv",
                data / "state" / "scrape-state.json",
                data / "update-summary.json",
            ]
            checked = []

            def inspect_then_fail(source, target):
                checked.append(target)
                replacement_targets = [live_targets[0], *live_targets[2:]]
                self.assertEqual(
                    source,
                    target.with_name(f".{target.name}.refresh.tmp"),
                )
                self.assertTrue(all(
                    path.with_name(f".{path.name}.refresh.tmp").is_file()
                    for path in replacement_targets
                ))
                self.assertTrue(all(
                    path.with_name(f".{path.name}.refresh.bak").is_file()
                    for path in live_targets
                ))
                raise OSError("inspect staged transaction")

            with self.assertRaisesRegex(OSError, "inspect staged transaction"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: (
                        SHEET_HEADER
                        + "SPSF-1,,,https://example.test/new,,,,\n"
                    ),
                    replacer=inspect_then_fail,
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(checked, [live_targets[0]])

    def test_failed_bootstrap_removes_transaction_created_empty_directories(self) -> None:
        """Absent targets and transaction-created directory trees are restored as absent."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            legacy = root / "legacy"
            output = root / "public"
            data = root / "private"
            write_legacy(legacy)
            before = snapshot_tree(root)

            with self.assertRaisesRegex(OSError, "bootstrap replace"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--legacy-dir",
                        str(legacy),
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                    replacer=lambda *args: (_ for _ in ()).throw(
                        OSError("bootstrap replace")
                    ),
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)
            self.assertFalse(output.exists())
            self.assertFalse(data.exists())

    def test_pre_mutation_parent_failure_never_unlinks_unsnapshotted_live_files(self) -> None:
        """Unknown snapshot state is not equivalent to an originally absent target."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (output / "data" / "update-summary.json").write_bytes(
                b"old public summary"
            )
            data.mkdir()
            (data / "raw").write_bytes(b"not a directory")
            before = snapshot_tree(root)

            with self.assertRaisesRegex(
                OSError, "transaction parent is not a directory"
            ):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: (
                        SHEET_HEADER
                        + "SPSF-1,,,https://example.test/new,,,,\n"
                    ),
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)

    def test_backup_staging_failure_preserves_unknown_target_and_original_error(self) -> None:
        """A failed backup is never trusted as rollback material for its live target."""
        from scripts import refresh as refresh_module

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            before = snapshot_tree(root)
            real_write_synced = refresh_module._write_synced

            def fail_sheet_backup(path, content):
                if path.name == ".sheet.csv.refresh.bak":
                    raise OSError("backup staging failed")
                return real_write_synced(path, content)

            with mock.patch(
                "scripts.refresh._write_synced",
                side_effect=fail_sheet_backup,
            ), self.assertRaisesRegex(OSError, "backup staging failed"):
                run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: (
                        SHEET_HEADER
                        + "SPSF-1,,,https://example.test/new,,,,\n"
                    ),
                    clock=lambda: GENERATED_AT,
                )

            self.assertEqual(snapshot_tree(root), before)

    def test_post_commit_cleanup_failure_is_nonfatal_after_successful_replacements(self) -> None:
        """Best-effort staging cleanup cannot report a committed generation as failed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)

            with mock.patch(
                "scripts.refresh._cleanup_transaction_artifacts",
                side_effect=PermissionError("cleanup denied"),
            ):
                result = run_refresh(
                    [
                        "--mode",
                        "links-only",
                        "--output-root",
                        str(output),
                        "--data-root",
                        str(data),
                    ],
                    sheet_downloader=lambda *args, **kwargs: (
                        SHEET_HEADER
                        + "SPSF-1,,,https://example.test/new,,,,\n"
                    ),
                    clock=lambda: GENERATED_AT,
                )

            catalog = json.loads(
                (output / "data" / "catalog.json").read_text("utf-8")
            )
            self.assertTrue(result["changed"])
            self.assertEqual(
                catalog["series"][0]["videos"][0]["links"]["gofile"],
                "https://example.test/new",
            )
            self.assertFalse((output / "data" / "update-summary.json").exists())

    def test_successful_publish_replaces_every_target_after_validation(self) -> None:
        """The public and private generation commit only after the release gate passes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (output / "data" / "update-summary.json").write_bytes(
                b"stale public summary"
            )
            events = []

            def validate(candidate, previous, mode="incremental", refresh_context=None):
                events.append(("validate", candidate["generatedAt"]))
                return []

            def replace(source, target):
                events.append(("replace", target.relative_to(root).as_posix()))
                source.replace(target)

            def remove_stale(path):
                events.append(("remove", path.relative_to(root).as_posix()))
                path.unlink()

            result = run_refresh(
                [
                    "--mode",
                    "links-only",
                    "--legacy-dir",
                    str(root / "unused"),
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: (
                    SHEET_HEADER
                    + "SPSF-1,,,https://example.test/new,,,,\n"
                ),
                validator=validate,
                replacer=replace,
                stale_remover=remove_stale,
                clock=lambda: GENERATED_AT,
            )

            self.assertEqual(
                events,
                [
                    ("validate", GENERATED_AT),
                    ("replace", "public/data/catalog.json"),
                    ("remove", "public/data/update-summary.json"),
                    ("replace", "private/raw/products.json"),
                    ("replace", "private/raw/sheet.csv"),
                    ("replace", "private/raw/subtitles.json"),
                    ("replace", "private/state/scrape-state.json"),
                    ("replace", "private/update-summary.json"),
                ],
            )
            catalog = json.loads((output / "data" / "catalog.json").read_text("utf-8"))
            self.assertEqual(catalog["generatedAt"], result["internal"]["generatedAt"])
            self.assertEqual(catalog["refresh"]["counts"], result["publicRefresh"]["counts"])
            self.assertFalse((output / "data" / "update-summary.json").exists())

    def test_unchanged_authoritative_audit_dry_run_reports_publish_then_persists_evidence(self) -> None:
        """A complete audit is publishable evidence even when deployable content is identical."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (data / "raw").mkdir(parents=True)
            (data / "state").mkdir(parents=True)
            (data / "raw" / "products.json").write_bytes(b"old products")
            (data / "raw" / "sheet.csv").write_bytes(b"old sheet")
            (data / "state" / "scrape-state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "lastSuccessfulGeneration": "sha256:old",
                        "lastSuccessfulAt": "2026-07-28T00:00:00Z",
                        "official": {
                            "cursor": 1,
                            "complete": True,
                            "lastAuditAt": "2026-07-28T00:00:00Z",
                        },
                        "inputs": {},
                    }
                ),
                encoding="utf-8",
            )
            (data / "update-summary.json").write_bytes(b"old summary")

            def discover(existing, mode="incremental", **kwargs):
                self.assertEqual(mode, "audit")
                return [product()], {
                    "mode": "audit",
                    "pagesFetched": 1,
                    "parsedProducts": 1,
                    "newProducts": 0,
                    "knownProducts": 1,
                    "cursor": 1,
                    "retries": 0,
                    "errors": 0,
                    "stopReason": "empty",
                    "authoritativeComplete": True,
                }

            arguments = [
                "--mode",
                "audit",
                "--output-root",
                str(output),
                "--data-root",
                str(data),
            ]
            before = snapshot_tree(root)
            dry_result = run_refresh(
                arguments + ["--dry-run"],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                clock=lambda: GENERATED_AT,
            )

            self.assertTrue(dry_result["changed"])
            self.assertIn("DRY RUN CHANGED", dry_result["humanSummary"])
            self.assertEqual(snapshot_tree(root), before)

            result = run_refresh(
                arguments,
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                featured_cover_refresher=lambda *args: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            state = json.loads(
                (data / "state" / "scrape-state.json").read_text("utf-8")
            )
            report = json.loads((data / "update-summary.json").read_text("utf-8"))
            self.assertTrue(result["changed"])
            self.assertEqual(state["official"]["lastAuditAt"], GENERATED_AT)
            self.assertEqual(report["sources"]["official"]["authoritativeComplete"], True)
            self.assertNotEqual(snapshot_tree(root), before)

    def test_authoritative_audit_publishes_legacy_archive_retention_evidence(self) -> None:
        """Private update evidence must make archive-only retention explicit and deterministic."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            official = [
                product(f"LIVE-{number}", productId=number)
                for number in range(1, 11)
            ]
            write_previous(
                output,
                official
                + [product("ARCH-2", productId=None), product("ARCH-1", productId=None)],
            )

            def discover(existing, mode="incremental", **kwargs):
                self.assertEqual(mode, "audit")
                return official, {
                    "mode": "audit",
                    "pagesFetched": 1,
                    "parsedProducts": 10,
                    "newProducts": 0,
                    "knownProducts": 10,
                    "cursor": 1,
                    "retries": 0,
                    "errors": 0,
                    "stopReason": "empty",
                    "authoritativeComplete": True,
                }

            result = run_refresh(
                [
                    "--mode",
                    "audit",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                featured_cover_refresher=lambda *args: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            catalog = json.loads((output / "data" / "catalog.json").read_text("utf-8"))
            report = json.loads((data / "update-summary.json").read_text("utf-8"))
            published_codes = sorted(
                video["code"]
                for series in catalog["series"]
                for video in series["videos"]
            )
            self.assertEqual(len(published_codes), 12)
            self.assertIn("ARCH-1", published_codes)
            self.assertIn("ARCH-2", published_codes)
            self.assertEqual(report["counts"]["archiveRetained"], 2)
            self.assertEqual(report["codes"]["archiveRetained"], ["ARCH-1", "ARCH-2"])
            self.assertTrue(result["publicRefresh"]["sourceComplete"])
            self.assertNotIn("archiveRetained", result["publicRefresh"]["counts"])

    def test_incremental_content_publish_preserves_last_authoritative_audit_timestamp(self) -> None:
        """A later non-audit publication must retain the latest successful audit time."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (data / "state").mkdir(parents=True)
            prior_audit_at = "2026-07-28T12:34:56Z"
            write_current_subtitle_state(
                data,
                {
                    "schemaVersion": 1,
                    "lastSuccessfulGeneration": "sha256:old",
                    "lastSuccessfulAt": "2026-07-28T12:34:56Z",
                    "official": {
                        "cursor": 1,
                        "complete": True,
                        "lastAuditAt": prior_audit_at,
                    },
                    "inputs": {},
                },
            )

            result = run_refresh(
                [
                    "--mode",
                    "incremental",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=lambda existing, mode="incremental", **kwargs: (
                    [product("SPSF-2", productId=2)],
                    {
                        "mode": "incremental",
                        "pagesFetched": 2,
                        "parsedProducts": 1,
                        "newProducts": 1,
                        "knownProducts": 0,
                        "cursor": 2,
                        "retries": 0,
                        "errors": 0,
                        "stopReason": "empty",
                        "authoritativeComplete": False,
                    },
                ),
                featured_cover_refresher=lambda *args: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            state = json.loads(
                (data / "state" / "scrape-state.json").read_text("utf-8")
            )
            self.assertTrue(result["changed"])
            self.assertEqual(state["official"]["lastAuditAt"], prior_audit_at)

    def test_incomplete_audit_with_tail_additions_is_a_noop(self) -> None:
        """A non-authoritative full audit must publish neither additions nor evidence."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            prior_audit_at = "2026-07-28T12:34:56Z"
            (data / "state" / "scrape-state.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "lastSuccessfulGeneration": "sha256:old",
                        "lastSuccessfulAt": "2026-07-28T12:34:56Z",
                        "official": {
                            "cursor": 1,
                            "complete": True,
                            "lastAuditAt": prior_audit_at,
                        },
                        "inputs": {},
                    }
                ),
                encoding="utf-8",
            )
            before = snapshot_tree(root)

            def discover(existing, mode="incremental", **kwargs):
                if mode == "audit":
                    return [], {
                        "mode": "audit",
                        "pagesFetched": 1,
                        "parsedProducts": 0,
                        "newProducts": 0,
                        "knownProducts": 0,
                        "cursor": 1,
                        "retries": 3,
                        "errors": 1,
                        "stopReason": "error",
                        "error": "network_retries_exhausted",
                        "authoritativeComplete": False,
                    }
                return [product("SPSF-2", productId=2)], {
                    "mode": "tail",
                    "pagesFetched": 0,
                    "parsedProducts": 1,
                    "newProducts": 1,
                    "knownProducts": 0,
                    "cursor": 5,
                    "retries": 0,
                    "errors": 0,
                    "tailProbes": 4,
                    "tailMisses": 3,
                    "stopReason": "three_misses",
                    "authoritativeComplete": False,
                }

            result = run_refresh(
                [
                    "--mode",
                    "audit",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=discover,
                featured_cover_refresher=lambda *args: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            self.assertFalse(result["changed"])
            self.assertFalse(result["publicRefresh"]["sourceComplete"])
            self.assertEqual(snapshot_tree(root), before)

    def test_unchanged_incremental_refresh_remains_a_noop(self) -> None:
        """Incremental source evidence alone must not create daily publication churn."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            output, data = seed_publish_targets(root)
            prior_audit_at = "2026-07-28T12:34:56Z"
            write_current_subtitle_state(
                data,
                {
                    "schemaVersion": 1,
                    "lastSuccessfulGeneration": "sha256:old",
                    "lastSuccessfulAt": "2026-07-28T12:34:56Z",
                    "official": {
                        "cursor": 1,
                        "complete": True,
                        "lastAuditAt": prior_audit_at,
                    },
                    "inputs": {},
                },
            )
            before = snapshot_tree(root)

            result = run_refresh(
                [
                    "--mode",
                    "incremental",
                    "--output-root",
                    str(output),
                    "--data-root",
                    str(data),
                ],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                discoverer=lambda existing, mode="incremental", **kwargs: (
                    [],
                    {
                        "mode": "incremental",
                        "pagesFetched": 2,
                        "parsedProducts": 1,
                        "newProducts": 0,
                        "knownProducts": 1,
                        "cursor": 2,
                        "retries": 0,
                        "errors": 0,
                        "stopReason": "all_known",
                        "authoritativeComplete": False,
                    },
                ),
                featured_cover_refresher=lambda *args: {"published": False},
                clock=lambda: GENERATED_AT,
            )

            self.assertFalse(result["changed"])
            self.assertIn("UNCHANGED", result["humanSummary"])
            self.assertEqual(snapshot_tree(root), before)

    def test_noop_refresh_reports_unchanged_and_writes_nothing(self) -> None:
        """Bookkeeping timestamps alone must not create daily deployment commits."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "public"
            data = root / "private"
            write_previous(output)
            (data / "raw").mkdir(parents=True)
            (data / "state").mkdir(parents=True)
            (data / "raw" / "products.json").write_bytes(b"old products")
            (data / "raw" / "sheet.csv").write_bytes(b"old sheet")
            write_current_subtitle_state(data)
            (data / "update-summary.json").write_bytes(b"old summary")
            before = snapshot_tree(root)
            arguments = [
                "--mode",
                "links-only",
                "--legacy-dir",
                str(root / "unused"),
                "--output-root",
                str(output),
                "--data-root",
                str(data),
            ]

            result = run_refresh(
                arguments,
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                clock=lambda: GENERATED_AT,
            )
            dry_result = run_refresh(
                arguments + ["--dry-run"],
                sheet_downloader=lambda *args, **kwargs: SHEET_HEADER,
                clock=lambda: GENERATED_AT,
            )

            self.assertFalse(result["changed"])
            self.assertFalse(dry_result["changed"])
            self.assertIn("UNCHANGED", result["humanSummary"])
            self.assertIn("UNCHANGED", dry_result["humanSummary"])
            self.assertEqual(snapshot_tree(root), before)
            self.assertEqual(list(root.rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
