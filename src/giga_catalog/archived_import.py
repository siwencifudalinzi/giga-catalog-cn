"""Build catalog-ready records for verified titles removed from GIGA's directory."""

from __future__ import annotations

import csv
import copy
import re
from io import StringIO
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlparse

from src.giga_catalog.codes import normalize_code
from src.giga_catalog.merge import build_catalog


_CODE_AT_START = re.compile(r"^\s*([A-Za-z][A-Za-z0-9]*[-_ ]\d+)")
_PROVIDERS = {
    "STREAMTAPE LINK": "streamtape",
    "PLAYER4ME LINK": "player4me",
    "GOFILE LINK": "gofile",
}


def build_archived_products(
    confirmed_rows: Sequence[Mapping[str, str]],
    details_by_code: Mapping[str, Mapping[str, object]],
) -> list[dict]:
    products = []
    for source in confirmed_rows:
        code = normalize_code(source.get("规范番号"))
        if code is None or code not in details_by_code:
            continue
        detail = details_by_code[code]
        detail_url = str(detail.get("detailUrl") or "")
        if detail_url != str(source.get("AsiaMonstr详情页") or ""):
            continue
        previews = _unique_https_previews(detail.get("previewImages"))
        if not previews:
            continue
        products.append(
            {
                "actors": [],
                "code": code,
                "cover": str(source.get("官方资源HEAD地址") or ""),
                "previewImages": previews,
                "releaseDate": None,
                "title": str(source.get("标题") or "").strip(),
            }
        )
    return sorted(products, key=lambda item: item["code"])


def parse_archived_sheet_links(text: str, target_codes: Iterable[str]) -> dict[str, dict]:
    targets = {code for value in target_codes if (code := normalize_code(value))}
    rows = csv.reader(StringIO(text))
    header = [cell.lstrip("\ufeff").strip().upper() for cell in next(rows, [])]
    if "UNCENSORED" not in header:
        return {}
    uncensored_index = header.index("UNCENSORED")
    provider_columns = [
        (index, _PROVIDERS[name])
        for index, name in enumerate(header)
        if index > uncensored_index and name in _PROVIDERS
    ]
    result = {}
    for row in rows:
        raw = row[uncensored_index] if uncensored_index < len(row) else ""
        match = _CODE_AT_START.match(raw)
        code = normalize_code(match.group(1)) if match else None
        if code not in targets:
            continue
        links = {}
        for index, provider in provider_columns:
            value = row[index].strip() if index < len(row) else ""
            if _http_url(value):
                links[provider] = value
        if links:
            result[code] = {"uncensored": dict(sorted(links.items()))}
    return dict(sorted(result.items()))


def merge_archived_catalog(
    previous_catalog: Mapping[str, object],
    archived_products: Sequence[Mapping[str, object]],
    archived_links: Mapping[str, Mapping[str, object]],
    *,
    generated_at: str,
) -> tuple[dict, dict]:
    existing_products = []
    selected_links = {}
    archived_codes = {
        code
        for item in archived_products
        if (code := normalize_code(item.get("code"))) is not None
    }
    for series in previous_catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if not isinstance(video, Mapping):
                continue
            product = copy.deepcopy(dict(video))
            code = normalize_code(product.get("code"))
            links = product.pop("links", None)
            if code is not None and isinstance(links, Mapping) and links:
                selected_links[code] = copy.deepcopy(dict(links))
            if code not in archived_codes:
                existing_products.append(product)
    for code, links in archived_links.items():
        selected_links[code] = copy.deepcopy(dict(links))

    previous_refresh = previous_catalog.get("refresh")
    previous_inputs = (
        previous_refresh.get("inputs")
        if isinstance(previous_refresh, Mapping)
        and isinstance(previous_refresh.get("inputs"), Mapping)
        else None
    )
    return build_catalog(
        existing_products + [copy.deepcopy(dict(item)) for item in archived_products],
        selected_links,
        generated_at=generated_at,
        previous_catalog=previous_catalog,
        refresh_context={
            "mode": "incremental",
            "scanComplete": False,
            **({"inputs": copy.deepcopy(dict(previous_inputs))} if previous_inputs else {}),
        },
    )


def _unique_https_previews(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        source = item.strip()
        parsed = urlparse(source)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == "www.asiamonstr.com"
            and parsed.path.startswith("/wp-content/uploads/")
            and not parsed.query
            and not parsed.fragment
        ):
            # AsiaMonstr's upload host only serves these legacy HTTP assets in a
            # browser.  WordPress' image CDN supplies an HTTPS-safe pass-through
            # while keeping the original AsiaMonstr path visible and auditable.
            url = f"https://i0.wp.com/www.asiamonstr.com{parsed.path}"
        else:
            continue
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _http_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)
