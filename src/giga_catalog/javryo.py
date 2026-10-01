"""Safe metadata parsers for the JAVRyo catalog and its external landing links."""

from __future__ import annotations

import html as html_module
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from src.giga_catalog.codes import normalize_code


JAVRYO_ORIGIN = "https://javryo.com"
STREAMTAPE_PATH_RE = re.compile(r"^/(?:v|e)/[A-Za-z0-9_-]+(?:/[^/?#]*)?/?$")


def extract_sitemap_urls(xml_text: str) -> list[str]:
    """Return canonical JAVRyo movie URLs from one WordPress sitemap."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    urls = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
            continue
        value = node.text.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == "javryo.com"
            and re.fullmatch(r"/movies/[a-z0-9][a-z0-9-]*/", parsed.path)
            and not parsed.query
            and not parsed.fragment
        ):
            urls.append(value)
    return sorted(set(urls))


def extract_movie_code(url: str) -> Optional[str]:
    """Extract the leading GIGA-style code from a JAVRyo movie slug."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "javryo.com":
        return None
    match = re.fullmatch(r"/movies/([a-z][a-z0-9]*)-(\d+)(?:-[a-z0-9-]+)?/", parsed.path)
    return normalize_code(f"{match.group(1)}-{match.group(2)}") if match else None


class _MovieParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.post_id: Optional[int] = None
        self.wrapper_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta" and values.get("property", "").lower() == "og:title":
            self.title = values.get("content", "").strip()
        post = values.get("data-post", "")
        if post.isdigit():
            self.post_id = int(post)
        if tag.lower() == "a":
            absolute = urljoin(JAVRYO_ORIGIN + "/", values.get("href", ""))
            parsed = urlsplit(absolute)
            if (
                parsed.scheme == "https"
                and (parsed.hostname or "").lower() == "javryo.com"
                and re.fullmatch(r"/links/[a-z0-9]+/", parsed.path)
            ):
                self.wrapper_urls.append(absolute)


def extract_movie_page(html_text: str) -> dict:
    parser = _MovieParser()
    parser.feed(html_text)
    return {
        "title": parser.title,
        "postId": parser.post_id,
        "wrapperUrls": list(dict.fromkeys(parser.wrapper_urls)),
    }


class _WrapperParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and values.get("id") == "link":
            self.href = values.get("href", "").strip()


def extract_wrapper_target(html_text: str) -> Optional[str]:
    parser = _WrapperParser()
    parser.feed(html_text)
    if not parser.href:
        return None
    href = html_module.unescape(parser.href)
    target = parse_qs(urlsplit(href).query).get("s", [""])[0]
    target = unquote(target).strip()
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return target


def streamtape_page_is_live(url: str, status_code: int, html_text: str) -> bool:
    """Conservatively accept only a present Streamtape watch page."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (
        status_code != 200
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "streamtape.com"
        or not STREAMTAPE_PATH_RE.fullmatch(parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        return False
    lowered = html_text.lower()
    deleted_markers = ("video not found", "file was deleted", "file not found")
    return not any(marker in lowered for marker in deleted_markers) and (
        "streamtape" in lowered and ("<video" in lowered or ".mp4" in lowered)
    )


def _overlay_entries(overlay: object) -> dict:
    if not isinstance(overlay, dict) or not isinstance(overlay.get("entries"), dict):
        return {}
    return overlay["entries"]


def apply_overlay_to_catalog(catalog: dict, overlay: object) -> int:
    """Add canonical JAVRyo page links without changing any existing provider."""
    entries = _overlay_entries(overlay)
    changed = 0
    link_added = 0
    link_updated = 0
    for series in catalog.get("series", []) if isinstance(catalog, dict) else []:
        for video in series.get("videos", []) if isinstance(series, dict) else []:
            if not isinstance(video, dict):
                continue
            code = normalize_code(video.get("code"))
            entry = entries.get(code) if code else None
            page_url = entry.get("pageUrl") if isinstance(entry, dict) else None
            if (
                not isinstance(page_url, str)
                or extract_movie_code(page_url) != code
            ):
                continue
            links = video.setdefault("links", {})
            if not isinstance(links, dict):
                continue
            if links.get("javryo") != page_url:
                was_linked = bool(links)
                links["javryo"] = page_url
                changed += 1
                if was_linked:
                    link_updated += 1
                else:
                    link_added += 1
    if isinstance(catalog.get("totals"), dict):
        linked_videos = sum(
            bool(video.get("links"))
            for series in catalog.get("series", [])
            for video in series.get("videos", [])
            if isinstance(video, dict)
        )
        catalog["totals"]["linkedVideos"] = linked_videos
        refresh = catalog.get("refresh")
        counts = refresh.get("counts") if isinstance(refresh, dict) else None
        if isinstance(counts, dict):
            counts["linked"] = linked_videos
            if isinstance(counts.get("linkAdded"), int):
                counts["linkAdded"] += link_added
            if isinstance(counts.get("linkUpdated"), int):
                counts["linkUpdated"] += link_updated
    return changed


def build_manifest_entries(overlay: object, embed_verification: object = None,
                           streamtape_verification: object = None,
                           embed_candidates: object = None) -> dict:
    """Publish only two-path playback-verified stable player landings."""
    from .javryo_streams import normalize_embed_url
    from .javryo_embeds_browser import VERIFICATION_VERSION, classify_observation, classify_source_observation
    from .resolved_links import validate_final_url, source_url_hash

    embed_rows = embed_verification.get("results", {}) if isinstance(embed_verification, dict) else {}
    tape_rows = streamtape_verification.get("results", {}) if isinstance(streamtape_verification, dict) else {}
    candidate_rows = embed_candidates.get("entries", {}) if isinstance(embed_candidates, dict) else {}
    entries = {}
    for code, item in sorted(_overlay_entries(overlay).items()):
        if not isinstance(item, dict):
            continue
        canonical = normalize_code(code)
        source_url = item.get("pageUrl")
        if canonical != code or not isinstance(source_url, str) or extract_movie_code(source_url) != code:
            continue
        digest = source_url_hash(source_url)
        selected = None
        for provider, row in (("streamtape", tape_rows.get(code)), ("javryo_stream", embed_rows.get(code))):
            if (not isinstance(row, dict) or row.get("sourceUrlHash") != digest
                    or row.get("verificationVersion") != VERIFICATION_VERSION):
                continue
            final_url = validate_final_url(row.get("finalUrl"), expected_provider=provider)
            paths = row.get("paths", {})
            if (row.get("playbackStatus") != "verified" or not final_url
                    or not isinstance(paths, dict)
                    or not isinstance(paths.get("source"), dict)
                    or not isinstance(paths.get("direct"), dict)
                    or paths.get("source", {}).get("status") not in {"reached", "verified"}
                    or paths.get("direct", {}).get("status") != "verified"
                    or not isinstance(row.get("checkedAt"), str) or not row["checkedAt"]):
                continue
            direct_evidence = paths["direct"].get("evidence", {})
            source_evidence = paths["source"].get("evidence", {})
            if (not isinstance(direct_evidence, dict) or not isinstance(source_evidence, dict)
                    or classify_observation(direct_evidence) != "verified"
                    or (classify_source_observation(source_evidence) if provider == "javryo_stream"
                        else classify_observation(source_evidence))
                       != ("reached" if provider == "javryo_stream" else "verified")):
                continue
            if provider == "streamtape" and final_url != item.get("streamtapeUrl"):
                continue
            if provider == "javryo_stream":
                inventory = candidate_rows.get(code)
                if (row.get("embedUrlHash") != source_url_hash(final_url)
                        or not isinstance(inventory, dict)
                        or inventory.get("sourceUrlHash") != digest
                        or normalize_embed_url(inventory.get("embedUrl")) != final_url):
                    continue
            selected = (provider, final_url, row["checkedAt"])
            break
        if not selected:
            continue
        provider, final_url, checked_at = selected
        entries[code] = {
            "standard.javryo": {
                "provider": provider,
                "sourceUrlHash": digest,
                "finalUrl": final_url,
                "kind": "external",
                "status": "verified",
                "playbackStatus": "verified",
                "checkedAt": checked_at,
            }
        }
    return entries
