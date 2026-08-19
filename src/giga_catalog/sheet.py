"""Import provider links from the public spreadsheet CSV export."""

import csv
from io import StringIO
import time
from urllib.parse import urlparse

import requests

from src.giga_catalog.codes import normalize_code


_PROVIDER_HEADERS = {
    "STREAMTAPE LINK": "streamtape",
    "PLAYER4ME LINK": "player4me",
    "GOFILE LINK": "gofile",
}
_TRANSIENT_STATUS_CODES = {408, 425, 429}


class SheetFormatError(ValueError):
    """The downloaded CSV does not match the required public-sheet schema."""


def download_sheet(
    url: str,
    timeout: float = 30,
    retries: int = 3,
    delay_seconds: float = 1.0,
) -> str:
    """Download and decode a public spreadsheet CSV export."""
    if retries <= 0:
        raise ValueError("retries must be positive")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException:
            if attempt == retries:
                raise
        else:
            transient = (
                response.status_code in _TRANSIENT_STATUS_CODES
                or 500 <= response.status_code < 600
            )
            if not transient:
                response.raise_for_status()
                if not 200 <= response.status_code < 300:
                    raise requests.HTTPError(
                        f"unexpected HTTP status {response.status_code}",
                        response=response,
                    )
                return response.text
            if attempt == retries:
                response.raise_for_status()
        if delay_seconds > 0:
            time.sleep(delay_seconds * (2 ** (attempt - 1)))
    raise RuntimeError("sheet download retries were exhausted")


def download_sheet_bytes(
    url: str,
    timeout: float = 30,
    retries: int = 3,
    delay_seconds: float = 1.0,
    max_bytes: int = 8 * 1024 * 1024,
) -> bytes:
    """Download exact source bytes with the text downloader's retry policy."""
    if retries <= 0:
        raise ValueError("retries must be positive")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    for attempt in range(1, retries + 1):
        response = None
        try:
            response = requests.get(url, timeout=timeout, stream=True)
        except requests.RequestException:
            if attempt == retries:
                raise
        else:
            transient = (
                response.status_code in _TRANSIENT_STATUS_CODES
                or 500 <= response.status_code < 600
            )
            if not transient:
                response.raise_for_status()
                if not 200 <= response.status_code < 300:
                    raise requests.HTTPError(
                        f"unexpected HTTP status {response.status_code}",
                        response=response,
                    )
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    if not content_length.isdigit():
                        raise ValueError("binary response Content-Length is invalid")
                    if int(content_length) > max_bytes:
                        raise ValueError("binary response exceeds maximum size")
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("binary response exceeds maximum size")
                    chunks.append(chunk)
                return b"".join(chunks)
            if attempt == retries:
                response.raise_for_status()
        finally:
            if response is not None:
                response.close()
        if delay_seconds > 0:
            time.sleep(delay_seconds * (2 ** (attempt - 1)))
    raise RuntimeError("binary sheet download retries were exhausted")


def parse_sheet_csv(text: str) -> tuple[dict[str, dict], list[dict]]:
    """Map normal and uncensored provider links keyed by canonical product code."""
    rows = csv.reader(StringIO(text))
    header = next(rows, None)
    if not header:
        raise SheetFormatError("sheet CSV is empty")

    header = [_header_name(value) for value in header]
    code_index = _unique_header_index(header, "NEW CODE")
    uncensored_index = _unique_header_index(header, "UNCENSORED")
    if code_index >= uncensored_index:
        raise SheetFormatError(
            "sheet header NEW CODE must precede UNCENSORED"
        )

    normal_columns = _provider_columns(
        header,
        code_index + 1,
        uncensored_index,
        "normal",
    )
    uncensored_columns = _provider_columns(
        header,
        uncensored_index + 1,
        len(header),
        "uncensored",
    )
    links: dict[str, dict] = {}
    conflicts: list[dict] = []

    for row in rows:
        code = normalize_code(_cell(row, code_index))
        if code is None:
            continue

        if code in links:
            conflicts.append({"type": "duplicate_code", "code": code})
        record = links.setdefault(code, {})
        _import_group(row, record, normal_columns, code, conflicts)

        uncensored = record.get("uncensored")
        if uncensored is None:
            uncensored = {}
        _import_group(
            row,
            uncensored,
            uncensored_columns,
            code,
            conflicts,
            scope="uncensored.",
        )
        if uncensored:
            record["uncensored"] = uncensored

    return links, conflicts


def _header_name(value: str) -> str:
    return value.lstrip("\ufeff").strip().upper()


def _unique_header_index(header: list[str], name: str) -> int:
    indexes = [index for index, value in enumerate(header) if value == name]
    if len(indexes) != 1:
        raise SheetFormatError(
            f"sheet header must contain exactly one {name!r} column"
        )
    return indexes[0]


def _provider_columns(
    header: list[str],
    start: int,
    end: int,
    group: str,
) -> list[tuple[int, str]]:
    columns = []
    for name, provider in _PROVIDER_HEADERS.items():
        indexes = [
            index
            for index in range(start, end)
            if header[index] == name
        ]
        if len(indexes) != 1:
            raise SheetFormatError(
                f"sheet {group} columns must contain exactly one {name!r}"
            )
        columns.append((indexes[0], provider))
    return sorted(columns)


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _import_group(
    row: list[str],
    target: dict,
    columns: list[tuple[int, str]],
    code: str,
    conflicts: list[dict],
    scope: str = "",
) -> None:
    for index, provider in columns:
        value = _cell(row, index)
        if not value:
            continue
        diagnostic_provider = f"{scope}{provider}"
        if not _is_http_url(value):
            conflicts.append(
                {
                    "type": "invalid_url",
                    "code": code,
                    "provider": diagnostic_provider,
                    "url": value,
                }
            )
            continue

        existing = target.get(provider)
        if existing and existing != value:
            conflicts.append(
                {
                    "type": "conflict",
                    "code": code,
                    "provider": diagnostic_provider,
                    "existing": existing,
                    "incoming": value,
                }
            )
        target[provider] = value


def _is_http_url(value: str) -> bool:
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
