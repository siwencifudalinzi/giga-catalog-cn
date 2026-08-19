"""Derive compact, bounded GIGA preview descriptors from official cover URLs."""

import re
from typing import Optional
from urllib.parse import urlparse


DEFAULT_PREVIEW_COUNT = 18
_GIGA_HOST = "www.giga-web.jp"
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


def preview_base_from_cover(cover: object) -> Optional[str]:
    """Return the canonical sibling ``sample/`` directory for a GIGA cover."""
    if not isinstance(cover, str) or not cover.strip():
        return None
    parsed = urlparse(cover.strip())
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != _GIGA_HOST
        or parsed.query
        or parsed.fragment
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) < 3
        or parts[0].lower() != "db_titles"
        or parts[-1].lower() != "pac_s.jpg"
        or any(
            _SAFE_PATH_SEGMENT.fullmatch(part) is None
            for part in parts[1:-1]
        )
    ):
        return None

    directory = "/".join(parts[:-1])
    return f"https://{_GIGA_HOST}/{directory}/sample/"


def preview_descriptor_from_cover(
    cover: object,
    count: int = DEFAULT_PREVIEW_COUNT,
) -> dict:
    """Return a complete descriptor or an empty mapping for an untrusted cover."""
    base = preview_base_from_cover(cover)
    if (
        base is None
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count <= 0
    ):
        return {}
    return {"previewBase": base, "previewCount": count}


def is_giga_preview_base(value: object) -> bool:
    """Return whether a descriptor stays in an official GIGA sample directory."""
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    parts = [part for part in parsed.path.split("/") if part]
    return bool(
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() == _GIGA_HOST
        and not parsed.query
        and not parsed.fragment
        and parsed.path.endswith("/")
        and len(parts) >= 3
        and parts[0].lower() == "db_titles"
        and parts[-1].lower() == "sample"
        and all(
            _SAFE_PATH_SEGMENT.fullmatch(part) is not None
            for part in parts[1:-1]
        )
    )
