"""Canonical product-code helpers."""

import re
from typing import Optional


_CODE_PATTERN = re.compile(r"\s*([A-Za-z][A-Za-z0-9]*)[\s_-](\d+)\s*")


def normalize_code(value: str) -> Optional[str]:
    """Return an uppercase ``PREFIX-NUMBER`` key, or ``None`` for non-codes."""
    if not isinstance(value, str):
        return None

    match = _CODE_PATTERN.fullmatch(value)
    if match is None:
        return None

    prefix, suffix = match.groups()
    number = int(suffix)
    if number < 0:
        return None
    return f"{prefix.upper()}-{number}"
