"""Stable JAVRyo player candidates and their strict public URL boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .javryo import extract_movie_code
from .resolved_links import source_url_hash


EMBED_PATHS = {
    "bysejikuar.com": re.compile(r"/e/[A-Za-z0-9_-]+"),
    "ryonanation.icu": re.compile(r"/(?:v|p)/[A-Za-z0-9_-]+"),
    "ryonads.icu": re.compile(r"/e/[A-Za-z0-9_-]+(?:\.html)?"),
    "short.icu": re.compile(r"/[A-Za-z0-9_-]+"),
    "dood.la": re.compile(r"/e/[A-Za-z0-9_-]+"),
    "player.mogulstream.icu": re.compile(r"/v/[A-Za-z0-9_-]+"),
    "movearnpre.com": re.compile(r"/embed/[A-Za-z0-9_-]+"),
    "javryo.embed4me.com": re.compile(r"/"),
}


def normalize_embed_url(value: object) -> Optional[str]:
    if (not isinstance(value, str) or value != value.strip() or len(value) > 2048
            or any(char in value for char in "\r\n\t")):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    pattern = EMBED_PATHS.get(host)
    if (parsed.scheme != "https" or not pattern or parsed.username or parsed.password
            or port not in (None, 443) or not pattern.fullmatch(parsed.path)):
        return None
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if (host != "short.icu" or len(pairs) != 1 or pairs[0][0] != "image"
                or not re.fullmatch(r"https://[^?#\s]+\.(?:jpe?g|png|webp)", pairs[0][1], re.I)):
            return None
    if host == "javryo.embed4me.com":
        if not re.fullmatch(r"[A-Za-z0-9]+", parsed.fragment):
            return None
    elif parsed.fragment:
        return None
    return urlunsplit(("https", host, parsed.path, "", parsed.fragment))


@dataclass(frozen=True)
class JavryoEmbedCandidate:
    code: str
    page_url: str
    post_id: int
    source_url_hash: str
    embed_url: Optional[str]
    host: str
    status: str
    fetched_at: str

    def public_record(self) -> dict:
        result = {
            "code": self.code, "pageUrl": self.page_url,
            "postId": self.post_id, "sourceUrlHash": self.source_url_hash,
            "status": self.status, "fetchedAt": self.fetched_at,
        }
        if self.embed_url:
            result["embedUrl"] = self.embed_url
            result["host"] = self.host
        return result


def collect_embed_candidates(crawl: Mapping, api_results: Mapping) -> list[JavryoEmbedCandidate]:
    crawl_rows = crawl.get("results", {}) if isinstance(crawl, Mapping) else {}
    api_rows = api_results.get("results", {}) if isinstance(api_results, Mapping) else {}
    candidates = []
    for code, row in sorted(api_rows.items()):
        if not isinstance(row, Mapping):
            continue
        source = crawl_rows.get(code) if isinstance(crawl_rows, Mapping) else None
        page_url = source.get("pageUrl", "") if isinstance(source, Mapping) else ""
        post_id = source.get("postId") if isinstance(source, Mapping) else None
        matching = (extract_movie_code(page_url) == code and isinstance(post_id, int)
                    and post_id > 0 and row.get("postId") == post_id and row.get("code") == code)
        embed = normalize_embed_url(row.get("embedUrl")) if matching and row.get("status") == "found" else None
        status = "ready" if embed else ("retryable" if matching and row.get("status") != "found" else "unsupported")
        candidates.append(JavryoEmbedCandidate(
            code=code, page_url=page_url if matching else "", post_id=post_id if matching else 0,
            source_url_hash=source_url_hash(page_url) if matching else "",
            embed_url=embed, host=(urlsplit(embed).hostname or "") if embed else "",
            status=status, fetched_at=str(row.get("checkedAt") or ""),
        ))
    return candidates
