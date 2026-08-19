"""Official GIGA tag parsing, normalization, and catalog helpers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Mapping, Sequence
from urllib.parse import parse_qs, urljoin, urlparse

from src.giga_catalog.scraper import (
    BASE_URL,
    _Node,
    _descendants,
    _parse_html,
    _text,
)


TAG_GROUPS = {"genre", "character"}
_GROUP_LABELS = {
    "ジャンルタグ": "genre",
    "キャラクタータグ": "character",
}


def product_detail_headers(base_url: str = BASE_URL) -> Dict[str, str]:
    """Return the same-site Referer required by current GIGA detail pages."""
    return {"Referer": f"{base_url.rstrip('/')}/search/"}


def parse_product_tags(html: str) -> List[dict]:
    """Extract the two official tag groups from one product detail page."""
    root = _parse_html(html)
    tags: List[dict] = []
    seen = set()
    for container in _descendants(root):
        if container.tag != "div" or container.attr("id") != "tag":
            continue
        header = _find_direct_child_by_id(container, "tag_header")
        group = _GROUP_LABELS.get(_text(header))
        if group is None:
            continue
        main = _find_direct_child_by_id(container, "tag_main")
        for tag in _tags_from_container(main, group):
            if tag["id"] not in seen:
                tags.append(tag)
                seen.add(tag["id"])
    return tags


def parse_tag_directory(html: str, group: str) -> List[dict]:
    """Extract the stable ID/name pairs from one complete official tag list."""
    if group not in TAG_GROUPS:
        raise ValueError(f"unknown tag group: {group}")
    root = _parse_html(html)
    return _tags_from_container(root, group)


def normalize_tag_definitions(
    definitions: Iterable[Mapping[str, object]],
    reviewed_translations: Mapping[str, str] = None,
) -> List[dict]:
    """Return one deterministic definition per positive official tag ID."""
    overrides = reviewed_translations or {}
    by_id = {}
    for source in definitions:
        if not isinstance(source, Mapping):
            continue
        tag_id = source.get("id")
        group = source.get("group")
        name_ja = _clean_name(source.get("nameJa"))
        if (
            not isinstance(tag_id, int)
            or isinstance(tag_id, bool)
            or tag_id <= 0
            or group not in TAG_GROUPS
            or not name_ja
        ):
            continue
        prior = by_id.get(tag_id)
        if prior and (prior["group"] != group or prior["nameJa"] != name_ja):
            raise ValueError(f"conflicting official tag definition: {tag_id}")
        name_zh = _clean_name(overrides.get(name_ja))
        source_name = "reviewed" if name_zh else _clean_name(source.get("nameZh"))
        if not name_zh:
            name_zh = source_name or name_ja
            translation_source = _clean_name(source.get("translationSource")) or (
                "official" if name_zh == name_ja else "machine"
            )
        else:
            translation_source = "reviewed"
        by_id[tag_id] = {
            "id": tag_id,
            "group": group,
            "nameJa": name_ja,
            "nameZh": name_zh,
            "translationSource": translation_source,
        }
    return [by_id[tag_id] for tag_id in sorted(by_id)]


def build_public_tag_index(
    products: Sequence[Mapping[str, object]],
    definitions: Sequence[Mapping[str, object]],
) -> List[dict]:
    """Build the compact public dictionary and verify every video reference."""
    normalized = normalize_tag_definitions(definitions)
    known = {tag["id"]: tag for tag in normalized}
    counts = Counter()
    for product in products:
        code = str(product.get("code") or "unknown")
        tag_ids = product.get("tagIds", [])
        if not isinstance(tag_ids, list):
            raise ValueError(f"{code} tagIds must be an array")
        for tag_id in set(tag_ids):
            if tag_id not in known:
                raise ValueError(f"{code} references unknown tag id {tag_id}")
            counts[tag_id] += 1
    public = []
    for tag in normalized:
        public.append(
            {
                "id": tag["id"],
                "group": tag["group"],
                "nameJa": tag["nameJa"],
                "nameZh": tag["nameZh"],
                "count": counts[tag["id"]],
            }
        )
    return public


def _find_direct_child_by_id(node: _Node, element_id: str):
    for child in node.children:
        if isinstance(child, _Node) and child.attr("id") == element_id:
            return child
    return None


def _tags_from_container(container: _Node, group: str) -> List[dict]:
    if container is None:
        return []
    tags: List[dict] = []
    seen = set()
    for node in _descendants(container):
        if node.tag != "a":
            continue
        tag_id = _tag_id_from_href(node.attr("href"))
        name = re.sub(r"\s+", " ", _text(node)).strip()
        if tag_id is None or not name or tag_id in seen:
            continue
        tags.append({"id": tag_id, "group": group, "nameJa": name})
        seen.add(tag_id)
    return tags


def _tag_id_from_href(href: str):
    if not isinstance(href, str) or not href.strip():
        return None
    parsed = urlparse(urljoin(f"{BASE_URL}/search/tag.php", href))
    if not parsed.path.rstrip("/").endswith("/search/index.php"):
        return None
    values = parse_qs(parsed.query).get("tag_id", [])
    if len(values) != 1 or not values[0].isdigit():
        return None
    value = int(values[0])
    return value if value > 0 else None


def _clean_name(value: object) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
