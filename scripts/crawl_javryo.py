"""Crawl JAVRyo metadata for catalog-matching titles without downloading media."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import requests


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.giga_catalog.codes import normalize_code  # noqa: E402
from src.giga_catalog.javryo import (  # noqa: E402
    extract_movie_code,
    extract_movie_page,
    extract_sitemap_urls,
    extract_wrapper_target,
    streamtape_page_is_live,
)
from src.giga_catalog.resolved_links import atomic_write_json  # noqa: E402


SITEMAP_INDEX = "https://javryo.com/wp-sitemap.xml"
USER_AGENT = "GigaCatalogMetadataAudit/1.0 (+https://siwencifudalinzi.github.io/giga-catalog-cn/)"
PRINT_LOCK = threading.Lock()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--catalog", type=Path, default=REPOSITORY_ROOT / "public/data/catalog.json")
    result.add_argument("--state", type=Path, default=REPOSITORY_ROOT / "data/state/javryo-crawl.json")
    result.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "data/javryo-links.json")
    result.add_argument("--workers", type=int, default=4)
    result.add_argument("--timeout", type=float, default=25.0)
    result.add_argument("--report-every", type=int, default=300)
    result.add_argument("--limit", type=int)
    result.add_argument("--apply", action="store_true", help="apply completed results locally")
    return result


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _catalog_codes(catalog: object) -> set[str]:
    result = set()
    if not isinstance(catalog, dict):
        return result
    for series in catalog.get("series", []):
        for video in series.get("videos", []) if isinstance(series, dict) else []:
            code = normalize_code(video.get("code")) if isinstance(video, dict) else None
            if code:
                result.add(code)
    return result


def _xml_locations(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [
        node.text.strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "loc" and node.text and node.text.strip()
    ]


def _get_text(url: str, timeout: float) -> tuple[int, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.5"}
    last_status = 0
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, stream=True)
            last_status = response.status_code
            chunks = []
            size = 0
            for chunk in response.iter_content(32768, decode_unicode=False):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= 512_000:
                    break
            raw = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            text = raw.decode(encoding, errors="replace")
            if response.status_code not in (429, 500, 502, 503, 504, 530):
                return response.status_code, text
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return last_status, ""


def _discover_movies(timeout: float) -> list[str]:
    status, body = _get_text(SITEMAP_INDEX, timeout)
    if status != 200:
        raise RuntimeError(f"sitemap index returned HTTP {status}")
    sitemap_urls = [
        value for value in _xml_locations(body)
        if "/wp-sitemap-posts-movies-" in value
    ]
    movies = []
    for sitemap_url in sitemap_urls:
        status, body = _get_text(sitemap_url, timeout)
        sitemap_movies = extract_sitemap_urls(body)
        # This WordPress host has been observed returning a false 404 status
        # with a complete, valid sitemap body, so the XML contents are decisive.
        if not sitemap_movies:
            raise RuntimeError(f"movie sitemap returned HTTP {status}: {sitemap_url}")
        movies.extend(sitemap_movies)
    return sorted(set(movies))


def _crawl_one(code: str, page_url: str, timeout: float) -> dict:
    checked_at = _now()
    status, body = _get_text(page_url, timeout)
    result = {
        "code": code,
        "pageUrl": page_url,
        "pageStatus": status,
        "checkedAt": checked_at,
        "title": "",
        "postId": None,
        "wrapperUrls": [],
        "targets": [],
        "streamtapeUrl": "",
        "status": "page_failed" if status != 200 else "matched",
    }
    if status != 200:
        return result
    metadata = extract_movie_page(body)
    result.update(metadata)
    targets = []
    for wrapper_url in metadata["wrapperUrls"]:
        wrapper_status, wrapper_body = _get_text(wrapper_url, timeout)
        target = extract_wrapper_target(wrapper_body) if wrapper_status == 200 else None
        targets.append({"wrapperUrl": wrapper_url, "status": wrapper_status, "target": target or ""})
    result["targets"] = targets
    for candidate in (item["target"] for item in targets):
        if (urlsplit(candidate).hostname or "").lower() != "streamtape.com":
            continue
        target_status, target_body = _get_text(candidate, timeout)
        if streamtape_page_is_live(candidate, target_status, target_body):
            result["streamtapeUrl"] = candidate
            result["status"] = "streamtape_verified"
            break
    return result


def _overlay(results: dict) -> dict:
    return {
        "schemaVersion": 1,
        "generatedAt": _now(),
        "source": SITEMAP_INDEX,
        "entries": {
            code: {
                "pageUrl": item["pageUrl"],
                "title": item.get("title", ""),
                "streamtapeUrl": item.get("streamtapeUrl", ""),
                "checkedAt": item.get("checkedAt", ""),
                "status": item.get("status", ""),
            }
            for code, item in sorted(results.items())
            if item.get("pageStatus") == 200
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    options = parser().parse_args(list(argv) if argv is not None else None)
    catalog_codes = _catalog_codes(_read_json(options.catalog, {}))
    movie_urls = _discover_movies(options.timeout)
    matches = {}
    duplicates = []
    for url in movie_urls:
        code = extract_movie_code(url)
        if not code or code not in catalog_codes:
            continue
        if code in matches:
            duplicates.append({"code": code, "urls": [matches[code], url]})
            continue
        matches[code] = url
    state = _read_json(options.state, {})
    prior = state.get("results", {}) if isinstance(state, dict) else {}
    results = dict(prior) if isinstance(prior, dict) else {}
    pending = [
        (code, url) for code, url in sorted(matches.items())
        if not isinstance(results.get(code), dict) or results[code].get("pageUrl") != url
    ]
    resumed = len(matches) - len(pending)
    if options.limit is not None:
        pending = pending[: max(0, options.limit)]
    print(
        f"JAVRyo discovered={len(movie_urls)} matched={len(matches)} "
        f"resumed={resumed} pending={len(pending)}",
        flush=True,
    )
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, min(options.workers, 8))) as executor:
        futures = {
            executor.submit(_crawl_one, code, url, options.timeout): code
            for code, url in pending
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as error:  # preserve progress and retry next run
                results[code] = {
                    "code": code,
                    "pageUrl": matches[code],
                    "pageStatus": 0,
                    "checkedAt": _now(),
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                }
            completed += 1
            state_payload = {
                "schemaVersion": 1,
                "updatedAt": _now(),
                "catalogCount": len(catalog_codes),
                "discoveredCount": len(movie_urls),
                "matchedCount": len(matches),
                "duplicates": duplicates,
                "results": results,
            }
            atomic_write_json(options.state, state_payload)
            atomic_write_json(options.output, _overlay(results))
            if completed % max(1, options.report_every) == 0:
                verified = sum(item.get("status") == "streamtape_verified" for item in results.values())
                print(f"progress completed={completed}/{len(pending)} verifiedStreamtape={verified}", flush=True)
    verified = sum(item.get("status") == "streamtape_verified" for item in results.values())
    print(f"done matched={len(matches)} crawled={len(results)} verifiedStreamtape={verified}", flush=True)
    if options.apply and options.limit is None:
        from scripts.apply_javryo import apply_files
        applied = apply_files(REPOSITORY_ROOT)
        print("applied " + json.dumps(applied, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
