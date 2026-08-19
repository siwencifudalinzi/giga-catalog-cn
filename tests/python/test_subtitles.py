import copy
import hashlib
from io import BytesIO
import json
import unittest
from unittest import mock
from pathlib import Path
from typing import Optional
from zipfile import ZIP_DEFLATED, ZipFile

from src.giga_catalog import subtitles as subtitle_module
from src.giga_catalog.subtitles import (
    SubtitleFormatError,
    parse_subtitle_child_csv,
    parse_subtitle_directory_html,
)


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures"
DIRECTORY_FIXTURE = FIXTURE_DIR / "subtitle_directory.html"
CHILD_FIXTURE = FIXTURE_DIR / "subtitle_child.csv"
DIRECTORY_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/"
    "htmlview/sheet?pli=1&headers=true&gid=0"
)
CATALOG_SERIES = {"AHEF", "PGHD", "SPSE", "SPSF"}


def subtitle_directory_xlsx(
    *,
    sheet_name="giga collection",
    portal_target="https://ouo.io/BAbfv4",
    pink_target="https://ouo.io/2yaA66",
):
    """Build the smallest workbook that mirrors Google's public XLSX contract."""
    strings = [
        "NEW CODE",
        "STREAMTAPE LINK",
        "PLAYER4ME LINK",
        "GOFILE LINK",
        "UNCENSORED",
        "SRT ENGSUB DOWNLOAD",
        "PGHD",
        "PINK ENGSUB",
        "https://ouo.io/provider-link",
    ]
    shared_strings = "".join(f"<si><t>{value}</t></si>" for value in strings)
    cells = "".join(
        (
            '<c r="A1" s="0" t="s"><v>0</v></c>',
            '<c r="B1" s="0" t="s"><v>1</v></c>',
            '<c r="C1" s="0" t="s"><v>2</v></c>',
            '<c r="D1" s="0" t="s"><v>3</v></c>',
            '<c r="E1" s="0" t="s"><v>4</v></c>',
            '<c r="D10" s="2" t="s"><v>5</v></c>',
            '<c r="C38" s="1" t="s"><v>6</v></c>',
            '<c r="B60" s="0" t="s"><v>7</v></c>',
            # A pink provider URL must not be reclassified as a series source.
            '<c r="B4" s="1" t="s"><v>8</v></c>',
        )
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet state="visible" name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3">'
        '<font><color rgb="FF000000"/></font>'
        '<font><color rgb="FFFF00FF"/></font>'
        '<font><color rgb="FFFF0000"/></font>'
        '</fonts>'
        '<cellXfs count="3">'
        '<xf fontId="0"/><xf fontId="1"/><xf fontId="2"/>'
        '</cellXfs>'
        '</styleSheet>'
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheetData><row r="1">{cells}</row></sheetData>'
        '<hyperlinks>'
        '<hyperlink ref="D10" r:id="rPortal"/>'
        '<hyperlink ref="C38" r:id="rPink"/>'
        '<hyperlink ref="B4" r:id="rProvider"/>'
        '</hyperlinks>'
        '</worksheet>'
    )
    worksheet_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rPortal" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        f'Target="{portal_target}" TargetMode="External"/>'
        '<Relationship Id="rPink" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        f'Target="{pink_target}" TargetMode="External"/>'
        '<Relationship Id="rProvider" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        'Target="https://ouo.io/provider-link" TargetMode="External"/>'
        '</Relationships>'
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{shared_strings}</sst>",
        )
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            worksheet_relationships,
        )
    return output.getvalue()


def valid_subtitle_manifest():
    return {
        "schemaVersion": 1,
        "sourceUrl": DIRECTORY_URL,
        "legendColor": "#ff00ff",
        "portal": {
            "label": "SRT ENGSUB DOWNLOAD",
            "url": "https://ouo.io/BAbfv4",
        },
        "resolvedSources": [
            {
                "scope": "series",
                "series": "AHEF",
                "url": "https://drive.google.com/drive/folders/ahef",
            },
            {
                "scope": "video",
                "series": "SPSF",
                "code": "SPSF-44",
                "url": "https://drive.google.com/file/d/spsf44/view",
            },
            {
                "scope": "video",
                "series": "SPSF",
                "code": "SPSF-45",
                "url": "https://drive.google.com/drive/folders/spsf45",
            },
        ],
        "unresolvedSources": [
            {
                "series": "PGHD",
                "url": "https://ouo.io/2yaA66",
                "reason": "opaque_destination",
            },
            {
                "series": "SPSE",
                "url": "https://ouo.io/spse",
                "reason": "opaque_destination",
            },
        ],
        "childSources": [
            {
                "series": "SPSF",
                "sourceUrl": (
                    "https://docs.google.com/spreadsheets/d/child-spsf/"
                    "edit?gid=128#gid=128"
                ),
                "csvUrl": (
                    "https://docs.google.com/spreadsheets/d/child-spsf/"
                    "export?format=csv&gid=128"
                ),
                "links": {
                    "SPSF-44": "https://drive.google.com/file/d/spsf44/view",
                    "SPSF-45": "https://drive.google.com/drive/folders/spsf45",
                },
            }
        ],
    }


def signed_manifest(snapshot):
    canonical = copy.deepcopy(snapshot)
    canonical.pop("sha256", None)
    encoded = (
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return {**canonical, "sha256": hashlib.sha256(encoded).hexdigest()}


class SubtitleStateManifestTests(unittest.TestCase):
    def validator(self):
        validator = getattr(subtitle_module, "validate_subtitle_manifest", None)
        self.assertTrue(
            callable(validator),
            "subtitle state validation must have one reusable boundary",
        )
        return validator

    def test_accepts_valid_raw_and_signed_manifests_without_order_churn(self) -> None:
        """Reordering semantic source sets must not manufacture a new state digest."""
        validator = self.validator()
        raw = valid_subtitle_manifest()
        signed = signed_manifest(raw)
        reordered = copy.deepcopy(signed)
        reordered["resolvedSources"].reverse()
        reordered["unresolvedSources"].reverse()

        self.assertEqual(
            validator(raw, require_sha256=False),
            raw,
        )
        self.assertEqual(validator(reordered), signed)
        self.assertNotEqual(reordered["resolvedSources"], raw["resolvedSources"])

    def test_rejects_self_hashed_structural_url_and_cross_scope_violations(self) -> None:
        """A valid digest cannot bless malformed or contradictory source evidence."""
        validator = self.validator()
        cases = {}

        incomplete = {
            "legendColor": "#ff00ff",
            "resolvedSources": [],
        }
        cases["incomplete"] = incomplete

        unknown_top = valid_subtitle_manifest()
        unknown_top["unexpected"] = True
        cases["unknown top key"] = unknown_top

        wrong_version = valid_subtitle_manifest()
        wrong_version["schemaVersion"] = 2
        cases["unsupported version"] = wrong_version

        wrong_source = valid_subtitle_manifest()
        wrong_source["sourceUrl"] = DIRECTORY_URL.replace("gid=0", "gid=8")
        cases["wrong directory identity"] = wrong_source

        wrong_legend = valid_subtitle_manifest()
        wrong_legend["legendColor"] = "#fe00ff"
        cases["wrong legend"] = wrong_legend

        unsafe_portal = valid_subtitle_manifest()
        unsafe_portal["portal"] = {
            "label": "SRT ENGSUB DOWNLOAD",
            "url": "javascript:alert(1)",
        }
        cases["unsafe portal"] = unsafe_portal

        unknown_resolved_key = valid_subtitle_manifest()
        unknown_resolved_key["resolvedSources"][0]["kind"] = "folder"
        cases["unknown resolved key"] = unknown_resolved_key

        opaque_series = valid_subtitle_manifest()
        opaque_series["resolvedSources"][0]["url"] = "https://ouo.io/ahef"
        cases["series is not direct Drive"] = opaque_series

        wrong_video_code = valid_subtitle_manifest()
        wrong_video_code["resolvedSources"][1]["code"] = "PGHD-44"
        cases["video prefix mismatch"] = wrong_video_code

        duplicate_source = valid_subtitle_manifest()
        duplicate_source["resolvedSources"].append(
            copy.deepcopy(duplicate_source["resolvedSources"][1])
        )
        cases["duplicate resolved key"] = duplicate_source

        resolvable_unresolved = valid_subtitle_manifest()
        resolvable_unresolved["unresolvedSources"][0]["url"] = (
            "https://drive.google.com/open?id=pghd"
        )
        cases["unresolved Drive source"] = resolvable_unresolved

        wrong_child_export = valid_subtitle_manifest()
        wrong_child_export["childSources"][0]["csvUrl"] = (
            "https://docs.google.com/spreadsheets/d/child-spsf/"
            "export?format=csv&gid=999"
        )
        cases["child export mismatch"] = wrong_child_export

        orphan_video = valid_subtitle_manifest()
        orphan_video["childSources"][0]["links"].pop("SPSF-45")
        cases["video missing from child"] = orphan_video

        child_conflict = valid_subtitle_manifest()
        child_conflict["childSources"][0]["links"]["SPSF-44"] = (
            "https://drive.google.com/file/d/different/view"
        )
        cases["child and video URL conflict"] = child_conflict

        cross_scope = valid_subtitle_manifest()
        cross_scope["unresolvedSources"].append(
            {
                "series": "AHEF",
                "url": "https://ouo.io/ahef",
                "reason": "opaque_destination",
            }
        )
        cases["series occurs in resolved and unresolved"] = cross_scope

        empty_child_links = valid_subtitle_manifest()
        empty_child_links["childSources"][0]["links"] = {}
        cases["empty child links"] = empty_child_links

        for name, manifest in cases.items():
            with self.subTest(name=name), self.assertRaises(SubtitleFormatError):
                validator(signed_manifest(manifest))

    def test_structure_errors_precede_digest_validation(self) -> None:
        """Hash comparison must never run before the exact manifest schema is accepted."""
        validator = self.validator()
        malformed = valid_subtitle_manifest()
        malformed.pop("portal")
        malformed["sha256"] = "0" * 64

        with self.assertRaisesRegex(SubtitleFormatError, "keys"):
            validator(malformed)

        forged = signed_manifest(valid_subtitle_manifest())
        forged["sha256"] = "0" * 64
        with self.assertRaisesRegex(SubtitleFormatError, "sha256"):
            validator(forged)

        boolean_version = valid_subtitle_manifest()
        boolean_version["schemaVersion"] = True
        with self.assertRaisesRegex(SubtitleFormatError, "schemaVersion"):
            validator(signed_manifest(boolean_version))


class SubtitleDirectoryParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = DIRECTORY_FIXTURE.read_text(encoding="utf-8")

    def parse(
        self,
        html: Optional[str] = None,
        source_url: str = DIRECTORY_URL,
    ):
        return parse_subtitle_directory_html(
            self.html if html is None else html,
            source_url=source_url,
            catalog_series=CATALOG_SERIES,
        )

    def test_current_directory_keeps_only_the_pink_opaque_series_unresolved(self) -> None:
        """Changing a blue/red cell into a subtitle source would mispublish ordinary links."""
        directory = self.parse()

        self.assertEqual(directory.legend_color, "#ff00ff")
        self.assertEqual(directory.portal_url, "https://ouo.io/BAbfv4")
        self.assertEqual(directory.series_links, {})
        self.assertEqual(directory.child_sources, ())
        self.assertEqual(directory.pink_source_count, 1)
        self.assertEqual(len(directory.unresolved_sources), 1)
        self.assertEqual(directory.unresolved_sources[0].series, "PGHD")
        self.assertEqual(
            directory.unresolved_sources[0].url,
            "https://ouo.io/2yaA66",
        )
        self.assertEqual(
            directory.unresolved_sources[0].reason,
            "opaque_destination",
        )

    def test_xlsx_export_survives_dynamic_htmlview_without_the_removed_note(self) -> None:
        """Google's static HTML can disappear without breaking the XLSX source contract."""
        parser = getattr(subtitle_module, "parse_subtitle_directory_xlsx", None)
        self.assertTrue(callable(parser), "the XLSX directory parser is missing")

        directory = parser(
            subtitle_directory_xlsx(),
            source_url=DIRECTORY_URL,
            catalog_series=CATALOG_SERIES,
        )

        self.assertEqual(directory.legend_color, "#ff00ff")
        self.assertEqual(directory.portal_url, "https://ouo.io/BAbfv4")
        self.assertEqual(directory.series_links, {})
        self.assertEqual(directory.child_sources, ())
        self.assertEqual(directory.pink_source_count, 1)
        self.assertEqual(len(directory.unresolved_sources), 1)
        self.assertEqual(directory.unresolved_sources[0].series, "PGHD")
        self.assertEqual(directory.unresolved_sources[0].url, "https://ouo.io/2yaA66")

    def test_xlsx_export_fails_closed_on_wrong_workbook_or_missing_links(self) -> None:
        """A ZIP, wrong workbook, or missing source target cannot masquerade as the directory."""
        parser = getattr(subtitle_module, "parse_subtitle_directory_xlsx", None)
        self.assertTrue(callable(parser), "the XLSX directory parser is missing")
        broken_payloads = (
            b"not a workbook",
            subtitle_directory_xlsx(sheet_name="other sheet"),
            subtitle_directory_xlsx(portal_target=""),
            subtitle_directory_xlsx(pink_target=""),
        )

        for payload in broken_payloads:
            with self.subTest(size=len(payload)), self.assertRaises(
                SubtitleFormatError
            ):
                parser(
                    payload,
                    source_url=DIRECTORY_URL,
                    catalog_series=CATALOG_SERIES,
                )


    def test_css_color_controls_direct_drive_and_child_sheet_classification(self) -> None:
        """Classification must use the cell class's computed color, not URL type alone."""
        html = self.html.replace(
            '<td class="s_blue">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdrive.google.com%2Fdrive%2Ffolders%2Fahef-blue',
            '<td class="s_pink">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdrive.google.com%2Fdrive%2Ffolders%2Fahef-blue',
            1,
        ).replace(
            '<td class="s_red">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdocs.google.com%2Fspreadsheets%2Fd%2Fchild-spsf',
            '<td class="s_pink">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdocs.google.com%2Fspreadsheets%2Fd%2Fchild-spsf',
            1,
        )

        directory = self.parse(html)

        self.assertEqual(
            directory.series_links,
            {"AHEF": "https://drive.google.com/drive/folders/ahef-blue"},
        )
        self.assertEqual(len(directory.child_sources), 1)
        child = directory.child_sources[0]
        self.assertEqual(child.series, "SPSF")
        self.assertEqual(
            child.source_url,
            "https://docs.google.com/spreadsheets/d/child-spsf/edit?gid=128#gid=128",
        )
        self.assertEqual(
            child.csv_url,
            "https://docs.google.com/spreadsheets/d/child-spsf/export?format=csv&gid=128",
        )
        self.assertEqual(directory.pink_source_count, 3)

    def test_pink_drive_open_target_with_one_nonempty_id_is_series_level(self) -> None:
        """Rejecting Google's common open?id form would lose a valid future pink source."""
        html = self.html.replace(
            '<td class="s_blue">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdrive.google.com%2Fopen%3Fid%3Dspse-blue',
            '<td class="s_pink">\n              <a href="https://www.google.com/url?q=https%3A%2F%2Fdrive.google.com%2Fopen%3Fid%3Dspse-blue',
            1,
        )

        directory = self.parse(html)

        self.assertEqual(
            directory.series_links,
            {"SPSE": "https://drive.google.com/open?id=spse-blue"},
        )
        self.assertEqual(directory.pink_source_count, 2)

        malformed = html.replace("id%3Dspse-blue", "id%3D")
        malformed_directory = self.parse(malformed)
        self.assertEqual(malformed_directory.series_links, {})
        self.assertEqual(malformed_directory.unresolved_sources[0].series, "PGHD")
        self.assertEqual(malformed_directory.unresolved_sources[1].series, "SPSE")

    def test_rejects_wrong_sheet_identity_and_changed_directory_markers(self) -> None:
        """A wrong gid, login page, or changed legend must fail closed, not look empty."""
        wrong_gid = DIRECTORY_URL.replace("gid=0", "gid=82")
        broken_inputs = (
            (self.html, wrong_gid),
            ("<html><title>Sign in - Google Accounts</title></html>", DIRECTORY_URL),
            (self.html.replace('class="waffle"', 'class="grid"'), DIRECTORY_URL),
            (self.html.replace("PINK ENGSUB", "MAGENTA DOWNLOAD"), DIRECTORY_URL),
            (
                self.html.replace(
                    "PINK ENGSUB</td>",
                    "PINK ENGSUB</td><td>PINK ENGSUB</td>",
                ),
                DIRECTORY_URL,
            ),
            (self.html.replace("color: #ff00ff", "color: #fe00ff"), DIRECTORY_URL),
            (
                self.html.replace(
                    "note code name pink color added link",
                    "note code name magenta color added link",
                ),
                DIRECTORY_URL,
            ),
            (self.html.replace("SRT ENGSUB DOWNLOAD", "DOWNLOAD"), DIRECTORY_URL),
            (self.html.replace("NEW CODE", "CODE"), DIRECTORY_URL),
        )

        for html, source_url in broken_inputs:
            with self.subTest(source_url=source_url), self.assertRaises(
                SubtitleFormatError
            ):
                self.parse(html, source_url=source_url)


class SubtitleDirectoryDownloadTests(unittest.TestCase):
    def test_main_directory_uses_bounded_xlsx_export_while_children_stay_csv(self) -> None:
        """The dynamic htmlview must not be fetched as text, and child CSV stays text."""
        downloader = getattr(subtitle_module, "download_subtitle_source", None)
        self.assertTrue(callable(downloader), "the subtitle source downloader is missing")
        workbook_url = (
            "https://docs.google.com/spreadsheets/d/"
            "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/"
            "export?format=xlsx&gid=0"
        )
        child_url = (
            "https://docs.google.com/spreadsheets/d/child-spsf/"
            "export?format=csv&gid=128"
        )

        with mock.patch.object(
            subtitle_module,
            "download_sheet_bytes",
            return_value=b"xlsx",
        ) as binary_download, mock.patch.object(
            subtitle_module,
            "download_sheet",
            return_value="csv",
        ) as text_download:
            self.assertEqual(
                downloader(
                    DIRECTORY_URL,
                    timeout=9,
                    retries=5,
                    delay_seconds=0.125,
                ),
                b"xlsx",
            )
            self.assertEqual(
                downloader(
                    child_url,
                    timeout=7,
                    retries=4,
                    delay_seconds=0.25,
                ),
                "csv",
            )

        binary_download.assert_called_once_with(
            workbook_url,
            timeout=9,
            retries=5,
            delay_seconds=0.125,
            max_bytes=8 * 1024 * 1024,
        )
        text_download.assert_called_once_with(
            child_url,
            timeout=7,
            retries=4,
            delay_seconds=0.25,
        )


class SubtitleChildParserTests(unittest.TestCase):
    def test_normalizes_valid_codes_and_returns_one_url_per_catalog_video(self) -> None:
        """A code-normalization regression would silently miss or mis-key subtitle links."""
        links = parse_subtitle_child_csv(
            CHILD_FIXTURE.read_text(encoding="utf-8"),
            series="spsf",
            catalog_codes={"SPSF-44", "SPSF-45", "SPSF-46"},
        )

        self.assertEqual(
            links,
            {
                "SPSF-44": "https://drive.google.com/file/d/spsf44/view",
                "SPSF-45": "https://drive.google.com/drive/folders/spsf45",
            },
        )

    def test_rejects_empty_html_duplicate_unknown_wrong_prefix_and_invalid_urls(self) -> None:
        """Malformed child data must stop publication instead of partially importing rows."""
        malformed = (
            "",
            "<html><title>Sign in</title></html>",
            "CODE,SRT ENGSUB DOWNLOAD\n",
            "CODE,SRT ENGSUB DOWNLOAD\nSPSF-44,https://drive.test/a\nspsf_044,https://drive.test/b\n",
            "CODE,SRT ENGSUB DOWNLOAD\nSPSF-999,https://drive.test/a\n",
            "CODE,SRT ENGSUB DOWNLOAD\nPGHD-1,https://drive.test/a\n",
            "CODE,SRT ENGSUB DOWNLOAD\nSPSF-44,ftp://drive.test/a\n",
            "CODE,SRT ENGSUB DOWNLOAD\nSPSF-44,https://drive.test/a https://drive.test/b\n",
            "CODE,SRT ENGSUB DOWNLOAD,EXTRA\nSPSF-44,https://drive.test/a,https://drive.test/b\n",
        )

        for text in malformed:
            with self.subTest(text=text), self.assertRaises(SubtitleFormatError):
                parse_subtitle_child_csv(
                    text,
                    series="SPSF",
                    catalog_codes={"SPSF-44", "SPSF-45"},
                )


if __name__ == "__main__":
    unittest.main()
