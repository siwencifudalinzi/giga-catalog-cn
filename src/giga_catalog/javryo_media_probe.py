"""Stream bounded startup bytes in memory before supplying them to Chromium."""

from dataclasses import dataclass, field
import re
import threading
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError

MAX_MEDIA_BYTES = 262144
MAX_FETCH_BYTES = 524288
MAX_CONTEXT_BYTES = 2097152
MEDIA_PATH = re.compile(r"\.(?:mp4|m4v|ts|m4s)$", re.I)
SEGMENT_PATH = re.compile(r"\.(?:ts|m4s)$", re.I)
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)", re.I)


@dataclass
class MediaProbeBudget:
    allow_media: bool = True
    media_requests: int = 0
    bytes_read: int = 0
    lock: object = field(default_factory=threading.Lock)

    def claim_media(self) -> bool:
        with self.lock:
            if not self.allow_media or self.media_requests >= 2:
                return False
            self.media_requests += 1
            return True

    def read(self, raw, amount: int):
        with self.lock:
            amount = min(amount, MAX_CONTEXT_BYTES - self.bytes_read)
            if amount <= 0:
                return None
            self.bytes_read += amount
        chunk = raw.read(amount, decode_content=True)
        with self.lock:
            self.bytes_read -= amount - len(chunk)
        return chunk


def _range_header(headers: dict) -> str:
    match = re.fullmatch(r"bytes=(\d+)-(\d*)", headers.get("range", ""), re.I)
    if match:
        start = int(match[1])
        end = min(int(match[2]) if match[2] else start + MAX_MEDIA_BYTES - 1,
                  start + MAX_MEDIA_BYTES - 1)
        if end >= start:
            return f"bytes={start}-{end}"
    suffix = re.fullmatch(r"bytes=-(\d+)", headers.get("range", ""), re.I)
    if suffix and int(suffix[1]) > 0:
        return f"bytes=-{min(int(suffix[1]), MAX_MEDIA_BYTES)}"
    return f"bytes=0-{MAX_MEDIA_BYTES - 1}"


def fetch_probe(request: dict, budget: MediaProbeBudget):
    """Return a bounded response, or None without reading an unsafe media body."""
    url = request["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        return None
    known_media = request["resourceType"] == "media" or bool(MEDIA_PATH.search(parsed.path))
    if known_media and not budget.claim_media():
        return None
    headers = {key.lower(): value for key, value in request["headers"].items()
               if key.lower() not in {"host", "content-length", "accept-encoding"}}
    headers["accept-encoding"] = "identity"
    if request["method"] == "GET" and (known_media or request["resourceType"] in {"xhr", "fetch"}):
        headers["range"] = _range_header(headers)
    response = None
    try:
        response = requests.request(request["method"], url, headers=headers, data=request.get("data"),
                                    stream=True, allow_redirects=False, timeout=(5, 8))
        result_headers = {key.lower(): value for key, value in response.headers.items()}
        status = response.status_code
        if 300 <= status < 400:
            return {"status": status, "headers": result_headers, "body": b""}
        content_type = result_headers.get("content-type", "").lower()
        manifest = parsed.path.lower().endswith(".m3u8") or "mpegurl" in content_type
        media = not manifest and (known_media or content_type.startswith(("video/", "audio/"))
                                   or "application/octet-stream" in content_type)
        if media and not known_media and not budget.claim_media():
            return None
        limit = MAX_MEDIA_BYTES if media else (131072 if manifest else MAX_FETCH_BYTES)
        expected = None
        length = result_headers.get("content-length")
        if length is not None:
            if not re.fullmatch(r"\d+", length) or int(length) > limit:
                return None
            expected = int(length)
        if media and status in (200, 206):
            if status == 206:
                match = CONTENT_RANGE.fullmatch(result_headers.get("content-range", ""))
                if not match:
                    return None
                start, end, total = map(int, match.groups())
                size = end - start + 1
                if start > end or end >= total or size > limit or (expected is not None and size != expected):
                    return None
                if (parsed.path.lower().endswith((".mp4", ".m4v")) or "video/mp4" in content_type) and size == total:
                    return None
                expected = size
            elif not (expected is not None and (SEGMENT_PATH.search(parsed.path)
                         or "video/mp2t" in content_type or "audio/aac" in content_type)):
                # A server ignoring Range may be sending the complete film. Close at headers.
                return None
        body = bytearray()
        while len(body) < (expected if expected is not None else limit):
            remaining = (expected if expected is not None else limit) - len(body)
            chunk = budget.read(response.raw, min(65536, remaining))
            if chunk is None:
                return None
            if not chunk:
                break
            body.extend(chunk)
        if (expected is not None and len(body) != expected) or (expected is None and len(body) == limit):
            return None
        result_headers.pop("content-encoding", None)
        result_headers.pop("transfer-encoding", None)
        result_headers["content-length"] = str(len(body))
        result_headers["x-giga-bounded-probe"] = "media" if media else ("manifest" if manifest else "metadata")
        return {"status": status, "headers": result_headers, "body": bytes(body)}
    except (requests.RequestException, HTTPError, OSError, ValueError):
        return None
    finally:
        if response is not None:
            response.close()
