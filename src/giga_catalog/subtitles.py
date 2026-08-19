"""Fail-closed import helpers for the public subtitle directory."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
from io import BytesIO, StringIO
import json
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from src.giga_catalog.codes import normalize_code
from src.giga_catalog.sheet import download_sheet, download_sheet_bytes


SUBTITLE_SHEET_ID = "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag"
SUBTITLE_SHEET_GID = "0"
PINK_ENGSUB_COLOR = "#ff00ff"

_IDENTITY_LABELS = {
    "NEW CODE",
    "STREAMTAPE LINK",
    "PLAYER4ME LINK",
    "GOFILE LINK",
    "UNCENSORED",
}
_IDENTITY_NOTE = (
    "NOTE CODE NAME PINK COLOR ADDED LINK FOR DOWNLOAD SRT FROM GOOGLE DRIVE"
)
_PORTAL_LABEL = "SRT ENGSUB DOWNLOAD"
_LEGEND_LABEL = "PINK ENGSUB"
_SERIES_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_SHEET_PATH_PATTERN = re.compile(
    r"/spreadsheets/d/([A-Za-z0-9_-]+)(?:/([^/?#]+))?/?"
)
_MANIFEST_KEYS = {
    "schemaVersion",
    "sourceUrl",
    "legendColor",
    "portal",
    "resolvedSources",
    "unresolvedSources",
    "childSources",
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CELL_REFERENCE_PATTERN = re.compile(r"[A-Z]{1,3}[1-9][0-9]*")
_XLSX_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XLSX_DOCUMENT_REL_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
)
_XLSX_PACKAGE_REL_NS = (
    "{http://schemas.openxmlformats.org/package/2006/relationships}"
)
_XLSX_WORKSHEET_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
)
_XLSX_HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_MAX_XLSX_BYTES = 8 * 1024 * 1024
_MAX_XLSX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
_MAX_XLSX_ENTRIES = 100


class SubtitleFormatError(ValueError):
    """A subtitle source did not match the required public schema."""


@dataclass(frozen=True)
class SubtitleChildSource:
    """One explicitly pink child spreadsheet bounded to a catalog series."""

    series: str
    source_url: str
    csv_url: str


@dataclass(frozen=True)
class UnresolvedSubtitleSource:
    """One explicitly pink source that is not safe to publish as a link."""

    series: str
    url: str
    reason: str


@dataclass(frozen=True)
class SubtitleDirectory:
    """Validated public subtitle-directory contents."""

    legend_color: str
    portal_url: str
    series_links: Mapping[str, str]
    child_sources: Tuple[SubtitleChildSource, ...]
    unresolved_sources: Tuple[UnresolvedSubtitleSource, ...]
    pink_source_count: int


def validate_subtitle_manifest(
    value: object,
    *,
    require_sha256: bool = True,
) -> dict:
    """Validate and canonicalize one persisted or newly generated manifest."""
    if not isinstance(value, Mapping):
        raise SubtitleFormatError("subtitle manifest must be an object")
    expected_keys = _MANIFEST_KEYS | ({"sha256"} if require_sha256 else set())
    _require_exact_keys(value, expected_keys, "subtitle manifest")

    schema_version = value.get("schemaVersion")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise SubtitleFormatError("subtitle manifest schemaVersion must be 1")
    source_url = value.get("sourceUrl")
    if not isinstance(source_url, str):
        raise SubtitleFormatError("subtitle manifest sourceUrl must be a string")
    _validate_directory_url(source_url)
    if value.get("legendColor") != PINK_ENGSUB_COLOR:
        raise SubtitleFormatError("subtitle manifest legendColor is invalid")

    portal = value.get("portal")
    if not isinstance(portal, Mapping):
        raise SubtitleFormatError("subtitle manifest portal must be an object")
    _require_exact_keys(portal, {"label", "url"}, "subtitle manifest portal")
    if portal.get("label") != _PORTAL_LABEL:
        raise SubtitleFormatError("subtitle manifest portal label is invalid")
    portal_url = _plain_http_url(portal.get("url"))

    resolved = _validate_resolved_manifest_sources(value.get("resolvedSources"))
    unresolved = _validate_unresolved_manifest_sources(
        value.get("unresolvedSources")
    )
    children = _validate_child_manifest_sources(value.get("childSources"))
    _validate_manifest_source_relationships(resolved, unresolved, children)

    canonical = {
        "schemaVersion": 1,
        "sourceUrl": source_url,
        "legendColor": PINK_ENGSUB_COLOR,
        "portal": {
            "label": _PORTAL_LABEL,
            "url": portal_url,
        },
        "resolvedSources": sorted(
            resolved,
            key=lambda source: (
                source["scope"],
                source["series"],
                source.get("code", ""),
                source["url"],
            ),
        ),
        "unresolvedSources": sorted(
            unresolved,
            key=lambda source: (
                source["series"],
                source["url"],
                source["reason"],
            ),
        ),
        "childSources": sorted(
            children,
            key=lambda source: (
                source["series"],
                source["sourceUrl"],
                source["csvUrl"],
            ),
        ),
    }
    if not require_sha256:
        return canonical

    digest = value.get("sha256")
    if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
        raise SubtitleFormatError("subtitle manifest sha256 is invalid")
    expected_digest = _subtitle_manifest_sha256(canonical)
    if digest != expected_digest:
        raise SubtitleFormatError(
            "subtitle manifest sha256 does not match canonical manifest"
        )
    return {**canonical, "sha256": digest}


def _validate_resolved_manifest_sources(value: object) -> List[dict]:
    if not isinstance(value, list):
        raise SubtitleFormatError("subtitle manifest resolvedSources must be an array")
    resolved: List[dict] = []
    seen_keys = set()
    for index, source in enumerate(value):
        path = f"subtitle manifest resolvedSources[{index}]"
        if not isinstance(source, Mapping):
            raise SubtitleFormatError(f"{path} must be an object")
        scope = source.get("scope")
        if scope == "series":
            _require_exact_keys(source, {"scope", "series", "url"}, path)
            series = _canonical_series(source.get("series"), f"{path}.series")
            url = _plain_http_url(source.get("url"))
            if _destination_type(url) != "drive":
                raise SubtitleFormatError(f"{path}.url must be a direct Google Drive URL")
            key = ("series", series)
            canonical = {"scope": "series", "series": series, "url": url}
        elif scope == "video":
            _require_exact_keys(
                source,
                {"scope", "series", "code", "url"},
                path,
            )
            series = _canonical_series(source.get("series"), f"{path}.series")
            code = _canonical_video_code(source.get("code"), f"{path}.code")
            if code.rsplit("-", 1)[0] != series:
                raise SubtitleFormatError(f"{path}.code has the wrong series prefix")
            url = _plain_http_url(source.get("url"))
            key = ("video", series, code)
            canonical = {
                "scope": "video",
                "series": series,
                "code": code,
                "url": url,
            }
        else:
            raise SubtitleFormatError(f"{path}.scope is invalid")
        if key in seen_keys:
            raise SubtitleFormatError(f"duplicate subtitle resolved source key {key}")
        seen_keys.add(key)
        resolved.append(canonical)
    return resolved


def _validate_unresolved_manifest_sources(value: object) -> List[dict]:
    if not isinstance(value, list):
        raise SubtitleFormatError("subtitle manifest unresolvedSources must be an array")
    unresolved: List[dict] = []
    seen_series = set()
    for index, source in enumerate(value):
        path = f"subtitle manifest unresolvedSources[{index}]"
        if not isinstance(source, Mapping):
            raise SubtitleFormatError(f"{path} must be an object")
        _require_exact_keys(source, {"series", "url", "reason"}, path)
        series = _canonical_series(source.get("series"), f"{path}.series")
        url = _plain_http_url(source.get("url"))
        if source.get("reason") != "opaque_destination":
            raise SubtitleFormatError(f"{path}.reason is invalid")
        if _destination_type(url) != "opaque":
            raise SubtitleFormatError(f"{path}.url is not an opaque destination")
        if series in seen_series:
            raise SubtitleFormatError(
                f"duplicate subtitle unresolved source series {series}"
            )
        seen_series.add(series)
        unresolved.append(
            {
                "series": series,
                "url": url,
                "reason": "opaque_destination",
            }
        )
    return unresolved


def _validate_child_manifest_sources(value: object) -> List[dict]:
    if not isinstance(value, list):
        raise SubtitleFormatError("subtitle manifest childSources must be an array")
    children: List[dict] = []
    seen_series = set()
    seen_codes = set()
    for index, source in enumerate(value):
        path = f"subtitle manifest childSources[{index}]"
        if not isinstance(source, Mapping):
            raise SubtitleFormatError(f"{path} must be an object")
        _require_exact_keys(
            source,
            {"series", "sourceUrl", "csvUrl", "links"},
            path,
        )
        series = _canonical_series(source.get("series"), f"{path}.series")
        if series in seen_series:
            raise SubtitleFormatError(f"duplicate subtitle child series {series}")
        seen_series.add(series)

        source_url = _plain_http_url(source.get("sourceUrl"))
        if _destination_type(source_url) != "sheet":
            raise SubtitleFormatError(f"{path}.sourceUrl must be a Google Sheet")
        expected_csv_url = _child_csv_url(source_url)
        csv_url = _plain_http_url(source.get("csvUrl"))
        if csv_url != expected_csv_url:
            raise SubtitleFormatError(f"{path}.csvUrl does not match sourceUrl")

        links = source.get("links")
        if not isinstance(links, Mapping) or not links:
            raise SubtitleFormatError(f"{path}.links must be a non-empty object")
        canonical_links = {}
        for raw_code, raw_url in links.items():
            code = _canonical_video_code(raw_code, f"{path}.links code")
            if code.rsplit("-", 1)[0] != series:
                raise SubtitleFormatError(
                    f"{path}.links code {code} has the wrong series prefix"
                )
            if code in seen_codes:
                raise SubtitleFormatError(f"duplicate subtitle child code {code}")
            seen_codes.add(code)
            canonical_links[code] = _plain_http_url(raw_url)
        children.append(
            {
                "series": series,
                "sourceUrl": source_url,
                "csvUrl": csv_url,
                "links": {
                    code: canonical_links[code] for code in sorted(canonical_links)
                },
            }
        )
    return children


def _validate_manifest_source_relationships(
    resolved: Sequence[Mapping[str, object]],
    unresolved: Sequence[Mapping[str, object]],
    children: Sequence[Mapping[str, object]],
) -> None:
    direct_series = {
        source["series"] for source in resolved if source["scope"] == "series"
    }
    unresolved_series = {source["series"] for source in unresolved}
    child_series = {source["series"] for source in children}
    overlaps = (
        (direct_series & unresolved_series)
        | (direct_series & child_series)
        | (unresolved_series & child_series)
    )
    if overlaps:
        raise SubtitleFormatError(
            "subtitle series occurs in conflicting source scopes: "
            + ", ".join(sorted(str(series) for series in overlaps))
        )

    resolved_videos = {
        (source["series"], source["code"]): source["url"]
        for source in resolved
        if source["scope"] == "video"
    }
    child_videos = {
        (source["series"], code): url
        for source in children
        for code, url in source["links"].items()
    }
    if resolved_videos != child_videos:
        raise SubtitleFormatError(
            "subtitle resolved video sources must exactly match child source links"
        )


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set,
    path: str,
) -> None:
    if set(value) != expected:
        raise SubtitleFormatError(f"{path} keys are invalid")


def _canonical_series(value: object, path: str) -> str:
    normalized = _normalize_series(value) if isinstance(value, str) else None
    if normalized is None or normalized != value:
        raise SubtitleFormatError(f"{path} is not canonical")
    return normalized


def _canonical_video_code(value: object, path: str) -> str:
    normalized = normalize_code(value) if isinstance(value, str) else None
    if normalized is None or normalized != value:
        raise SubtitleFormatError(f"{path} is not canonical")
    return normalized


def _subtitle_manifest_sha256(value: Mapping[str, object]) -> str:
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def download_subtitle_source(
    url: str,
    timeout: float = 30,
    retries: int = 3,
    delay_seconds: float = 1.0,
) -> object:
    """Fetch the rich main workbook as bytes and child CSV sources as text."""
    try:
        _validate_directory_url(url)
    except SubtitleFormatError:
        return download_sheet(
            url,
            timeout=timeout,
            retries=retries,
            delay_seconds=delay_seconds,
        )
    return download_sheet_bytes(
        _directory_xlsx_url(),
        timeout=timeout,
        retries=retries,
        delay_seconds=delay_seconds,
        max_bytes=_MAX_XLSX_BYTES,
    )


def download_subtitle_text(
    url: str,
    timeout: float = 30,
    retries: int = 3,
    delay_seconds: float = 1.0,
) -> object:
    """Backward-compatible alias for the mixed subtitle source downloader."""
    return download_subtitle_source(
        url,
        timeout=timeout,
        retries=retries,
        delay_seconds=delay_seconds,
    )


def _directory_xlsx_url() -> str:
    return (
        "https://docs.google.com/spreadsheets/d/"
        f"{SUBTITLE_SHEET_ID}/export?format=xlsx&gid={SUBTITLE_SHEET_GID}"
    )


@dataclass
class _Anchor:
    href: str
    text_parts: List[str]

    @property
    def text(self) -> str:
        return _normalized_text("".join(self.text_parts))


@dataclass
class _Cell:
    classes: Tuple[str, ...]
    text_parts: List[str]
    anchors: List[_Anchor]
    font_color: Optional[str] = None

    @property
    def text(self) -> str:
        return _normalized_text("".join(self.text_parts))


class _DirectoryParser(HTMLParser):
    """Collect the bounded table cells and embedded CSS from Google htmlview."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: List[_Cell] = []
        self.style_parts: List[str] = []
        self.waffle_table_count = 0
        self._table_stack: List[bool] = []
        self._style_depth = 0
        self._cell: Optional[_Cell] = None
        self._anchor: Optional[_Anchor] = None

    @property
    def _inside_waffle(self) -> bool:
        return bool(self._table_stack and self._table_stack[-1])

    def handle_starttag(
        self,
        tag: str,
        attrs: Sequence[Tuple[str, Optional[str]]],
    ) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag == "style":
            self._style_depth += 1
            return
        if tag == "table":
            classes = set(attributes.get("class", "").split())
            is_waffle = "waffle" in classes
            if self._table_stack and self._table_stack[-1]:
                is_waffle = True
            self._table_stack.append(is_waffle)
            if "waffle" in classes:
                self.waffle_table_count += 1
            return
        if tag in {"td", "th"} and self._inside_waffle:
            self._cell = _Cell(
                classes=tuple(attributes.get("class", "").split()),
                text_parts=[],
                anchors=[],
                font_color=None,
            )
            return
        if tag == "a" and self._cell is not None:
            self._anchor = _Anchor(attributes.get("href", ""), [])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            return
        if tag == "a" and self._anchor is not None:
            if self._cell is not None:
                self._cell.anchors.append(self._anchor)
            self._anchor = None
            return
        if tag in {"td", "th"} and self._cell is not None:
            self.cells.append(self._cell)
            self._cell = None
            self._anchor = None
            return
        if tag == "table" and self._table_stack:
            self._table_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.style_parts.append(data)
        if self._cell is not None:
            self._cell.text_parts.append(data)
        if self._anchor is not None:
            self._anchor.text_parts.append(data)


def parse_subtitle_directory_html(
    text: str,
    *,
    source_url: str,
    catalog_series: Iterable[str],
) -> SubtitleDirectory:
    """Parse the authoritative rich-sheet view without following arbitrary links."""
    _validate_directory_url(source_url)
    if not isinstance(text, str) or not text.strip():
        raise SubtitleFormatError("subtitle directory HTML is empty")

    parser = _DirectoryParser()
    try:
        parser.feed(text)
        parser.close()
    except (TypeError, ValueError) as error:
        raise SubtitleFormatError("subtitle directory HTML is malformed") from error

    if parser.waffle_table_count != 1 or not parser.cells:
        raise SubtitleFormatError(
            "subtitle directory must contain exactly one waffle table"
        )

    class_colors = _parse_class_colors("".join(parser.style_parts))
    return _parse_subtitle_directory_cells(
        parser.cells,
        source_url=source_url,
        catalog_series=catalog_series,
        class_colors=class_colors,
        available_colors=class_colors.values(),
        require_identity_note=True,
    )


def parse_subtitle_directory_xlsx(
    payload: bytes,
    *,
    source_url: str,
    catalog_series: Iterable[str],
) -> SubtitleDirectory:
    """Parse Google's bounded XLSX export with its links and font colors intact."""
    _validate_directory_url(source_url)
    if not isinstance(payload, bytes) or not payload:
        raise SubtitleFormatError("subtitle directory XLSX is empty")
    if len(payload) > _MAX_XLSX_BYTES:
        raise SubtitleFormatError("subtitle directory XLSX is too large")

    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_XLSX_ENTRIES:
                raise SubtitleFormatError(
                    "subtitle directory XLSX contains too many entries"
                )
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise SubtitleFormatError(
                    "subtitle directory XLSX contains an encrypted entry"
                )
            if sum(entry.file_size for entry in entries) > _MAX_XLSX_UNCOMPRESSED_BYTES:
                raise SubtitleFormatError(
                    "subtitle directory XLSX expands beyond the safety limit"
                )

            worksheet_path = _xlsx_worksheet_path(archive)
            shared_strings = _xlsx_shared_strings(archive)
            style_colors = _xlsx_style_colors(archive)
            cells = _xlsx_cells(
                archive,
                worksheet_path,
                shared_strings,
                style_colors,
            )
    except SubtitleFormatError:
        raise
    except (BadZipFile, ElementTree.ParseError, KeyError, OSError, ValueError) as error:
        raise SubtitleFormatError("subtitle directory XLSX is malformed") from error

    if not cells:
        raise SubtitleFormatError("subtitle directory XLSX contains no cells")
    return _parse_subtitle_directory_cells(
        cells,
        source_url=source_url,
        catalog_series=catalog_series,
        class_colors={},
        available_colors=(color for color in style_colors if color is not None),
        require_identity_note=False,
    )


def _xlsx_worksheet_path(archive: ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.findall(f"{_XLSX_MAIN_NS}sheets/{_XLSX_MAIN_NS}sheet")
    if len(sheets) != 1:
        raise SubtitleFormatError(
            "subtitle directory XLSX must contain exactly one worksheet"
        )
    sheet = sheets[0]
    if sheet.get("name", "").strip().lower() != "giga collection":
        raise SubtitleFormatError("subtitle directory XLSX worksheet identity changed")
    if sheet.get("state", "visible") != "visible":
        raise SubtitleFormatError("subtitle directory XLSX worksheet is not visible")
    relationship_id = sheet.get(f"{_XLSX_DOCUMENT_REL_NS}id")
    if not relationship_id:
        raise SubtitleFormatError(
            "subtitle directory XLSX worksheet relationship is missing"
        )

    relationships = _xlsx_relationships(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    relationship = relationships.get(relationship_id)
    if (
        relationship is None
        or relationship[0] != _XLSX_WORKSHEET_REL_TYPE
        or relationship[2] not in {None, "Internal"}
    ):
        raise SubtitleFormatError(
            "subtitle directory XLSX worksheet relationship is invalid"
        )
    target = relationship[1].replace("\\", "/")
    if re.fullmatch(r"worksheets/sheet[1-9][0-9]*\.xml", target) is None:
        raise SubtitleFormatError(
            "subtitle directory XLSX worksheet target is unsafe"
        )
    return "xl/" + target


def _xlsx_shared_strings(archive: ZipFile) -> Tuple[str, ...]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall(f"{_XLSX_MAIN_NS}si"):
        strings.append(
            _normalized_text(
                "".join(
                    node.text or ""
                    for node in item.iter(f"{_XLSX_MAIN_NS}t")
                )
            )
        )
    return tuple(strings)


def _xlsx_style_colors(archive: ZipFile) -> Tuple[Optional[str], ...]:
    root = ElementTree.fromstring(archive.read("xl/styles.xml"))
    fonts = root.find(f"{_XLSX_MAIN_NS}fonts")
    cell_xfs = root.find(f"{_XLSX_MAIN_NS}cellXfs")
    if fonts is None or cell_xfs is None:
        raise SubtitleFormatError("subtitle directory XLSX styles are incomplete")

    font_colors: List[Optional[str]] = []
    for font in fonts.findall(f"{_XLSX_MAIN_NS}font"):
        color = font.find(f"{_XLSX_MAIN_NS}color")
        rgb = color.get("rgb") if color is not None else None
        if rgb is None:
            font_colors.append(None)
            continue
        if re.fullmatch(r"[0-9A-Fa-f]{8}", rgb) is None:
            raise SubtitleFormatError(
                "subtitle directory XLSX contains an invalid font color"
            )
        font_colors.append("#" + rgb[-6:].lower())

    style_colors: List[Optional[str]] = []
    for style in cell_xfs.findall(f"{_XLSX_MAIN_NS}xf"):
        raw_font_id = style.get("fontId")
        if raw_font_id is None or not raw_font_id.isdigit():
            raise SubtitleFormatError(
                "subtitle directory XLSX style has no valid font"
            )
        font_id = int(raw_font_id)
        if font_id >= len(font_colors):
            raise SubtitleFormatError(
                "subtitle directory XLSX style references a missing font"
            )
        style_colors.append(font_colors[font_id])
    if not style_colors:
        raise SubtitleFormatError("subtitle directory XLSX contains no cell styles")
    return tuple(style_colors)


def _xlsx_cells(
    archive: ZipFile,
    worksheet_path: str,
    shared_strings: Sequence[str],
    style_colors: Sequence[Optional[str]],
) -> List[_Cell]:
    worksheet = ElementTree.fromstring(archive.read(worksheet_path))
    relationships_path = (
        worksheet_path.rsplit("/", 1)[0]
        + "/_rels/"
        + worksheet_path.rsplit("/", 1)[1]
        + ".rels"
    )
    relationships = _xlsx_relationships(archive.read(relationships_path))
    hyperlink_targets: Dict[str, str] = {}
    for hyperlink in worksheet.findall(f".//{_XLSX_MAIN_NS}hyperlink"):
        reference = hyperlink.get("ref", "")
        relationship_id = hyperlink.get(f"{_XLSX_DOCUMENT_REL_NS}id", "")
        if _CELL_REFERENCE_PATTERN.fullmatch(reference) is None:
            raise SubtitleFormatError(
                "subtitle directory XLSX hyperlink reference is invalid"
            )
        relationship = relationships.get(relationship_id)
        if (
            relationship is None
            or relationship[0] != _XLSX_HYPERLINK_REL_TYPE
            or relationship[2] != "External"
        ):
            raise SubtitleFormatError(
                "subtitle directory XLSX hyperlink relationship is invalid"
            )
        if reference in hyperlink_targets:
            raise SubtitleFormatError(
                "subtitle directory XLSX contains duplicate cell hyperlinks"
            )
        hyperlink_targets[reference] = relationship[1]

    cells: List[_Cell] = []
    seen_references = set()
    for element in worksheet.findall(f".//{_XLSX_MAIN_NS}c"):
        reference = element.get("r", "")
        if (
            _CELL_REFERENCE_PATTERN.fullmatch(reference) is None
            or reference in seen_references
        ):
            raise SubtitleFormatError(
                "subtitle directory XLSX cell reference is invalid or duplicated"
            )
        seen_references.add(reference)

        raw_style = element.get("s", "0")
        if not raw_style.isdigit() or int(raw_style) >= len(style_colors):
            raise SubtitleFormatError(
                "subtitle directory XLSX cell style is invalid"
            )
        text = _xlsx_cell_text(element, shared_strings)
        target = hyperlink_targets.get(reference)
        anchors = [] if target is None else [_Anchor(target, [text])]
        cells.append(
            _Cell(
                classes=(),
                text_parts=[text],
                anchors=anchors,
                font_color=style_colors[int(raw_style)],
            )
        )

    missing_cells = sorted(set(hyperlink_targets) - seen_references)
    if missing_cells:
        raise SubtitleFormatError(
            "subtitle directory XLSX hyperlinks reference missing cells"
        )
    return cells


def _xlsx_cell_text(element, shared_strings: Sequence[str]) -> str:
    cell_type = element.get("t")
    if cell_type == "inlineStr":
        inline = element.find(f"{_XLSX_MAIN_NS}is")
        if inline is None:
            raise SubtitleFormatError(
                "subtitle directory XLSX inline string is missing"
            )
        return _normalized_text(
            "".join(
                node.text or "" for node in inline.iter(f"{_XLSX_MAIN_NS}t")
            )
        )

    value = element.find(f"{_XLSX_MAIN_NS}v")
    raw_value = (value.text or "") if value is not None else ""
    if cell_type == "s":
        if not raw_value.isdigit() or int(raw_value) >= len(shared_strings):
            raise SubtitleFormatError(
                "subtitle directory XLSX shared string reference is invalid"
            )
        return shared_strings[int(raw_value)]
    if cell_type not in {None, "str"}:
        raise SubtitleFormatError(
            "subtitle directory XLSX cell type is unsupported"
        )
    return _normalized_text(raw_value)


def _xlsx_relationships(payload: bytes) -> Dict[str, Tuple[str, str, Optional[str]]]:
    root = ElementTree.fromstring(payload)
    relationships: Dict[str, Tuple[str, str, Optional[str]]] = {}
    for relationship in root.findall(f"{_XLSX_PACKAGE_REL_NS}Relationship"):
        relationship_id = relationship.get("Id", "")
        relationship_type = relationship.get("Type", "")
        target = relationship.get("Target", "")
        target_mode = relationship.get("TargetMode")
        if not relationship_id or not relationship_type or not target:
            raise SubtitleFormatError(
                "subtitle directory XLSX relationship is incomplete"
            )
        if relationship_id in relationships:
            raise SubtitleFormatError(
                "subtitle directory XLSX relationship ID is duplicated"
            )
        relationships[relationship_id] = (
            relationship_type,
            target,
            target_mode,
        )
    return relationships


def _parse_subtitle_directory_cells(
    cells: Sequence[_Cell],
    *,
    source_url: str,
    catalog_series: Iterable[str],
    class_colors: Mapping[str, str],
    available_colors: Iterable[str],
    require_identity_note: bool,
) -> SubtitleDirectory:
    cell_labels = {cell.text.upper() for cell in cells if cell.text}
    missing_identity = sorted(_IDENTITY_LABELS - cell_labels)
    if missing_identity:
        raise SubtitleFormatError(
            "subtitle directory identity labels are missing: "
            + ", ".join(missing_identity)
        )
    if require_identity_note and not any(
        cell.text.upper().startswith(_IDENTITY_NOTE) for cell in cells
    ):
        raise SubtitleFormatError("subtitle directory identity note is missing")

    legend_cells = [
        cell for cell in cells if cell.text.upper() == _LEGEND_LABEL
    ]
    if len(legend_cells) != 1:
        raise SubtitleFormatError(
            "subtitle directory must contain exactly one PINK ENGSUB legend"
        )
    if PINK_ENGSUB_COLOR not in set(available_colors):
        raise SubtitleFormatError(
            "subtitle directory PINK ENGSUB color is unavailable"
        )
    legend_color = PINK_ENGSUB_COLOR

    portal_anchors = [
        anchor
        for cell in cells
        for anchor in cell.anchors
        if anchor.text.upper() == _PORTAL_LABEL
    ]
    if len(portal_anchors) != 1:
        raise SubtitleFormatError(
            "subtitle directory must contain exactly one global SRT portal"
        )
    portal_url = _resolved_http_url(portal_anchors[0].href)

    normalized_catalog_series = _normalized_series_set(catalog_series)
    series_links: Dict[str, str] = {}
    child_sources: List[SubtitleChildSource] = []
    unresolved_sources: List[UnresolvedSubtitleSource] = []
    seen_series = set()

    for cell in cells:
        if _cell_color(cell, class_colors) != legend_color:
            continue
        for anchor in cell.anchors:
            series = _normalize_series(anchor.text)
            if series is None:
                continue
            if series in seen_series:
                raise SubtitleFormatError(
                    f"duplicate pink subtitle source for series {series}"
                )
            seen_series.add(series)
            destination = _resolved_http_url(anchor.href)
            destination_type = _destination_type(destination)
            if (
                destination_type in {"drive", "sheet"}
                and series not in normalized_catalog_series
            ):
                raise SubtitleFormatError(
                    f"resolved pink subtitle series {series!r} is absent from the catalog"
                )
            if destination_type == "drive":
                series_links[series] = destination
            elif destination_type == "sheet":
                child_sources.append(
                    SubtitleChildSource(
                        series=series,
                        source_url=destination,
                        csv_url=_child_csv_url(destination),
                    )
                )
            else:
                unresolved_sources.append(
                    UnresolvedSubtitleSource(
                        series=series,
                        url=destination,
                        reason="opaque_destination",
                    )
                )

    return SubtitleDirectory(
        legend_color=legend_color,
        portal_url=portal_url,
        series_links=dict(sorted(series_links.items())),
        child_sources=tuple(sorted(child_sources, key=lambda source: source.series)),
        unresolved_sources=tuple(
            sorted(unresolved_sources, key=lambda source: source.series)
        ),
        pink_source_count=len(seen_series),
    )


def parse_subtitle_child_csv(
    text: str,
    *,
    series: str,
    catalog_codes: Iterable[str],
) -> Dict[str, str]:
    """Parse one explicitly pink child sheet into validated per-code links."""
    normalized_series = _normalize_series(series)
    if normalized_series is None:
        raise SubtitleFormatError(f"invalid child subtitle series {series!r}")
    if not isinstance(text, str) or not text.strip():
        raise SubtitleFormatError("subtitle child CSV is empty")
    if _looks_like_html(text):
        raise SubtitleFormatError("subtitle child source returned HTML")

    try:
        rows = csv.reader(StringIO(text), strict=True)
        header = next(rows, None)
    except csv.Error as error:
        raise SubtitleFormatError("subtitle child CSV is malformed") from error
    if header is None:
        raise SubtitleFormatError("subtitle child CSV is empty")
    normalized_header = tuple(_header_name(value) for value in header)
    if normalized_header != ("CODE", _PORTAL_LABEL):
        raise SubtitleFormatError(
            "subtitle child header must be exactly CODE,SRT ENGSUB DOWNLOAD"
        )

    catalog = set(catalog_codes)
    links: Dict[str, str] = {}
    try:
        for row_number, row in enumerate(rows, start=2):
            if not row or not any(value.strip() for value in row):
                continue
            if len(row) != 2:
                raise SubtitleFormatError(
                    f"subtitle child row {row_number} must contain exactly two columns"
                )
            code = normalize_code(row[0])
            if code is None:
                raise SubtitleFormatError(
                    f"subtitle child row {row_number} has an invalid code"
                )
            if code.split("-", 1)[0] != normalized_series:
                raise SubtitleFormatError(
                    f"subtitle child code {code} has the wrong series prefix"
                )
            if code not in catalog:
                raise SubtitleFormatError(
                    f"subtitle child code {code} is absent from the catalog"
                )
            if code in links:
                raise SubtitleFormatError(
                    f"duplicate normalized subtitle child code {code}"
                )
            links[code] = _plain_http_url(row[1].strip())
    except csv.Error as error:
        raise SubtitleFormatError("subtitle child CSV is malformed") from error

    if not links:
        raise SubtitleFormatError("subtitle child CSV contains no subtitle links")
    return dict(sorted(links.items()))


def _validate_directory_url(value: str) -> None:
    parsed = urlparse(value)
    expected_path = (
        f"/spreadsheets/d/{SUBTITLE_SHEET_ID}/htmlview/sheet"
    )
    gids = parse_qs(parsed.query, keep_blank_values=True).get("gid", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != "docs.google.com"
        or parsed.path != expected_path
        or gids != [SUBTITLE_SHEET_GID]
    ):
        raise SubtitleFormatError("subtitle directory source identity changed")


def _parse_class_colors(css: str) -> Dict[str, str]:
    colors: Dict[str, str] = {}
    for selector_group, declarations in re.findall(
        r"([^{}]+)\{([^{}]*)\}", css, flags=re.S
    ):
        match = re.search(
            r"(?:^|;)\s*color\s*:\s*([^;!]+)",
            declarations,
            flags=re.I,
        )
        if match is None:
            continue
        color = _normalize_color(match.group(1).strip())
        if color is None:
            continue
        for selector in selector_group.split(","):
            classes = re.findall(r"\.([A-Za-z_][\w-]*)", selector)
            if classes:
                colors[classes[-1]] = color
    return colors


def _normalize_color(value: str) -> Optional[str]:
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(character * 2 for character in value[1:])
    match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
        value,
    )
    if match is None:
        return None
    channels = tuple(int(channel) for channel in match.groups())
    if any(channel > 255 for channel in channels):
        return None
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _cell_color(cell: _Cell, colors: Mapping[str, str]) -> Optional[str]:
    if cell.font_color is not None:
        return cell.font_color
    color = None
    for class_name in cell.classes:
        if class_name in colors:
            color = colors[class_name]
    return color


def _resolved_http_url(value: str) -> str:
    direct = _plain_http_url(value)
    parsed = urlparse(direct)
    if parsed.hostname in {"google.com", "www.google.com"} and parsed.path == "/url":
        targets = parse_qs(parsed.query, keep_blank_values=True).get("q", [])
        if len(targets) != 1 or not targets[0]:
            raise SubtitleFormatError("Google redirect must contain exactly one q target")
        return _plain_http_url(targets[0])
    return direct


def _plain_http_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SubtitleFormatError("subtitle URL is empty")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise SubtitleFormatError("subtitle URL contains whitespace or controls")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SubtitleFormatError("subtitle URL must be HTTP(S)")
    return value


def _destination_type(value: str) -> str:
    parsed = urlparse(value)
    if _is_direct_drive_url(value):
        return "drive"
    if parsed.hostname == "docs.google.com" and _SHEET_PATH_PATTERN.fullmatch(
        parsed.path
    ):
        return "sheet"
    return "opaque"


def _is_direct_drive_url(value: str) -> bool:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "drive.google.com"
    ):
        return False
    if re.fullmatch(r"/drive/folders/[A-Za-z0-9_-]+/?", parsed.path):
        return True
    if re.fullmatch(r"/file/d/[A-Za-z0-9_-]+(?:/[^?#]*)?", parsed.path):
        return True
    if parsed.path != "/open":
        return False
    identifiers = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
    return len(identifiers) == 1 and bool(
        re.fullmatch(r"[A-Za-z0-9_-]+", identifiers[0])
    )


def _child_csv_url(value: str) -> str:
    parsed = urlparse(value)
    match = _SHEET_PATH_PATTERN.fullmatch(parsed.path)
    if parsed.hostname != "docs.google.com" or match is None:
        raise SubtitleFormatError("subtitle child source is not a Google Sheet")
    document_id = match.group(1)
    if document_id == SUBTITLE_SHEET_ID:
        raise SubtitleFormatError("subtitle child source cannot be the main directory")

    query_gid = parse_qs(parsed.query, keep_blank_values=True).get("gid", [])
    fragment_gid = parse_qs(parsed.fragment, keep_blank_values=True).get("gid", [])
    gids = query_gid + fragment_gid
    if any(not re.fullmatch(r"\d+", gid) for gid in gids):
        raise SubtitleFormatError("subtitle child gid must be numeric")
    distinct_gids = set(gids)
    if len(distinct_gids) > 1:
        raise SubtitleFormatError("subtitle child URL contains conflicting gids")
    gid = next(iter(distinct_gids), "0")
    return (
        f"https://docs.google.com/spreadsheets/d/{document_id}/export"
        f"?format=csv&gid={gid}"
    )


def _normalized_series_set(values: Iterable[str]) -> set:
    normalized = set()
    for value in values:
        series = _normalize_series(value)
        if series is None:
            raise SubtitleFormatError(f"catalog contains invalid series {value!r}")
        normalized.add(series)
    return normalized


def _normalize_series(value: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    compact = _normalized_text(value)
    if not _SERIES_PATTERN.fullmatch(compact):
        return None
    return compact.upper()


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _header_name(value: str) -> str:
    return _normalized_text(value.lstrip("\ufeff")).upper()


def _looks_like_html(value: str) -> bool:
    prefix = value.lstrip().lower()
    return prefix.startswith(("<!doctype html", "<html", "<head", "<body"))
