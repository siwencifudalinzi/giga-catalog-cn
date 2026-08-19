"""Official GIGA tag parsing, normalization, and catalog helpers."""

from __future__ import annotations

import re
from typing import Dict, List
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
