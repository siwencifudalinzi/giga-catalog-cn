"""Headless, bounded playback checks for stable JAVRyo embed pages."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlsplit

from .javryo_streams import JavryoEmbedCandidate, normalize_embed_url
from .resolved_links import source_url_hash


EVENT_SCRIPT = """(() => {
  window.__gigaPlaybackEvents = [];
  for (const name of ['playing', 'timeupdate'])
    document.addEventListener(name, () => {
      if (!window.__gigaPlaybackEvents.includes(name)) window.__gigaPlaybackEvents.push(name);
      for (const video of document.querySelectorAll('video')) video.pause();
    }, true);
})()"""
DELETED_RE = re.compile(r"no such file|file (?:was )?deleted|video not found|file not found", re.I)
CHALLENGE_RE = re.compile(r"captcha|cloudflare|just a moment|verify you are human|人机验证", re.I)
MEDIA_EXT_RE = re.compile(r"\.(?:mp4|m4v|ts|m4s)(?:$|[?])", re.I)
AD_HOST_RE = re.compile(r"^https?://(?:creative\.rmhfrtnd\.com|dcbbwymp1bhlf\.cloudfront\.net)/", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_evidence(observation: Mapping) -> dict:
    """Persist enumerated scalar evidence only; never network URL or response body."""
    keys = ("httpStatus", "videoCount", "duration", "manifestStatus", "manifestHost",
            "mediaStatus", "events", "challenge", "deleted", "errorCode",
            "embedStatus", "embedFrameSeen", "apiStatus", "clicks", "clickErrors")
    result = {key: observation[key] for key in keys if key in observation}
    if "events" in result:
        result["events"] = [event for event in result["events"] if event in ("playing", "timeupdate")]
    if "manifestHost" in result and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", str(result["manifestHost"])):
        result.pop("manifestHost")
    if "errorCode" in result:
        result["errorCode"] = str(result["errorCode"])[:64]
    for key in ("clicks", "clickErrors"):
        if key in result:
            result[key] = [str(value)[:64] for value in result[key][:5]]
    return result


def classify_observation(value: Mapping) -> str:
    status = value.get("httpStatus")
    if status in (404, 410) or value.get("embedStatus") in (404, 410) or value.get("deleted"):
        return "dead"
    if status in (401, 403, 429) or value.get("embedStatus") in (401, 403, 429) or value.get("challenge") or value.get("manifestStatus") in (401, 403, 429):
        return "blocked"
    if status != 200:
        return "retryable"
    if (value.get("videoCount", 0) > 0
            and (value.get("manifestStatus") == 200 or value.get("mediaStatus") in (200, 206))
            and any(event in ("playing", "timeupdate") for event in value.get("events", []))):
        return "verified"
    if value.get("videoCount", 0) > 0 and value.get("duration", 0) > 0 and value.get("manifestStatus") == 200:
        return "media_reachable"
    return "retryable"


def aggregate_path_status(paths: Mapping) -> str:
    if set(paths) != {"source", "direct"}:
        return "retryable"
    source = paths["source"].get("status")
    direct = paths["direct"].get("status")
    statuses = {source, direct}
    if source in {"reached", "verified"} and direct == "verified":
        return "verified"
    if source in {"reached", "verified", "media_reachable"} and direct in {"verified", "media_reachable"}:
        return "media_reachable"
    if "dead" in statuses:
        return "dead"
    if "blocked" in statuses:
        return "blocked"
    return "retryable"


def source_request_allowed(url: str, embed_host: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in {"javryo.com", embed_host}


async def _route_media(route):
    url = route.request.url
    if MEDIA_EXT_RE.search(urlsplit(url).path):
        # A bounded range is enough to observe startup; never fetch the whole file.
        headers = dict(route.request.headers)
        headers["range"] = "bytes=0-262143"
        await route.continue_(headers=headers)
    else:
        await route.continue_()


async def _observe(page, target_url: str, *, source_page: bool, timeout_ms: int,
                   navigate: bool = True, expected_embed: str = "") -> dict:
    observation = {"httpStatus": None, "videoCount": 0, "duration": 0, "events": []}
    media_requests = 0

    async def on_response(response):
        nonlocal media_requests
        path = urlsplit(response.url).path.lower()
        content_type = response.headers.get("content-type", "").lower()
        if source_page and "/wp-json/dooplayer/v1/post/" in urlsplit(response.url).path:
            observation["apiStatus"] = response.status
        if expected_embed and normalize_embed_url(response.url) == expected_embed:
            observation["embedStatus"] = response.status
        if path.endswith(".m3u8") or "mpegurl" in content_type:
            observation["manifestStatus"] = response.status
            observation["manifestHost"] = (urlsplit(response.url).hostname or "").lower()
        elif MEDIA_EXT_RE.search(path) and media_requests < 2:
            observation["mediaStatus"] = response.status
            media_requests += 1

    page.on("response", on_response)
    try:
        if navigate:
            response = await page.goto(target_url, wait_until="commit", timeout=timeout_ms)
            observation["httpStatus"] = response.status if response else None
        else:
            observation["httpStatus"] = 200 if page.url == target_url else None
        if source_page and observation["httpStatus"] == 200:
            try:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                await page.locator("#player-option-1").click(timeout=5000, no_wait_after=True)
                await page.wait_for_timeout(1500)
            except Exception:
                observation["errorCode"] = "source-player-click"
                # Playwright can report a click timeout after the page has already
                # issued the player API request. Keep watching for the exact iframe.
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        source_retry_at = asyncio.get_running_loop().time() + 5
        source_retried = False
        clicked = set()
        while asyncio.get_running_loop().time() < deadline:
            frames = list(page.frames)
            if source_page and expected_embed and (observation.get("embedStatus") == 200 or observation.get("apiStatus") == 200):
                if any(normalize_embed_url(frame.url) == expected_embed for frame in frames):
                    observation["embedFrameSeen"] = True
                    break
            if (source_page and not source_retried and not observation.get("apiStatus")
                    and asyncio.get_running_loop().time() >= source_retry_at):
                source_retried = True
                try:
                    await page.locator("#player-option-1").click(timeout=3000, no_wait_after=True)
                except Exception:
                    pass
            for frame in frames:
                if expected_embed:
                    ancestor = frame
                    allowed = False
                    while ancestor:
                        if normalize_embed_url(ancestor.url) == expected_embed:
                            allowed = True
                            break
                        ancestor = ancestor.parent_frame
                    if not allowed:
                        continue
                    observation["embedFrameSeen"] = True
                try:
                    frame_data = await frame.evaluate("""() => ({
                      events: window.__gigaPlaybackEvents || [],
                      count: document.querySelectorAll('video').length,
                      duration: Math.max(0, ...Array.from(document.querySelectorAll('video'), v =>
                        Number.isFinite(v.duration) ? v.duration : 0)),
                      text: (document.body?.innerText || '').slice(0, 500)
                    })""")
                except Exception:
                    continue
                observation["videoCount"] = max(observation["videoCount"], frame_data["count"])
                observation["duration"] = max(observation["duration"], frame_data["duration"])
                observation["events"] = list(set(observation["events"] + frame_data["events"]))
                if DELETED_RE.search(frame_data["text"]):
                    observation["deleted"] = True
                if CHALLENGE_RE.search(frame_data["text"]):
                    observation["challenge"] = True
                if observation["events"]:
                    continue
                for selector in (".captcha-gate__play", "#play", ".jw-icon-display", ".vjs-big-play-button",
                                 ".plyr__control--overlaid", "button[aria-label*='Play']",
                                 ".playbtn", "video"):
                    if (frame, selector) in clicked:
                        continue
                    try:
                        locator = frame.locator(selector).first
                        if await locator.count() and await locator.is_visible():
                            await locator.click(timeout=1500, no_wait_after=True,
                                                force=selector == ".captcha-gate__play")
                            clicked.add((frame, selector))
                            observation.setdefault("clicks", []).append(selector)
                            break
                    except Exception as error:
                        if len(observation.setdefault("clickErrors", [])) < 5:
                            observation["clickErrors"].append(f"{selector}:{type(error).__name__}")
                        continue
            if observation["events"] or observation.get("deleted") or observation.get("challenge"):
                break
            await page.wait_for_timeout(500)
    except Exception as error:
        observation["errorCode"] = type(error).__name__
    finally:
        page.remove_listener("response", on_response)
    return safe_evidence(observation)


async def verify_embed_candidate(candidate: JavryoEmbedCandidate, browser, *, timeout_ms: int = 12000) -> dict:
    if candidate.status != "ready" or not candidate.embed_url:
        return {"playbackStatus": "unsupported" if candidate.status == "unsupported" else "retryable"}
    paths = {}
    for path_name, url in (("source", candidate.page_url), ("direct", candidate.embed_url)):
        context = await browser.new_context(accept_downloads=False, service_workers="block")
        await context.add_init_script(EVENT_SCRIPT)
        await context.route(AD_HOST_RE, lambda route: route.abort())
        media_count = 0

        async def limited_media(route):
            nonlocal media_count
            media_count += 1
            if media_count > 2:
                await route.abort()
            else:
                await _route_media(route)

        await context.route(MEDIA_EXT_RE, limited_media)
        if path_name == "source":
            async def restrict_source(route):
                if source_request_allowed(route.request.url, candidate.host):
                    await route.continue_()
                else:
                    await route.abort()
            await context.route("**/*", restrict_source)
        page = await context.new_page()
        async def close_popup(opened):
            if opened is not page:
                try:
                    await opened.close()
                except Exception:
                    pass
        context.on("page", close_popup)
        try:
            evidence = await _observe(page, url, source_page=path_name == "source",
                                      timeout_ms=min(timeout_ms, 12000) if path_name == "source" else timeout_ms,
                                      expected_embed=candidate.embed_url if path_name == "source" else "")
            if (path_name == "source" and evidence.get("embedFrameSeen")
                    and (evidence.get("embedStatus") == 200 or evidence.get("apiStatus") == 200)):
                path_status = "reached"
            else:
                path_status = classify_observation(evidence)
                if path_name == "source" and path_status == "verified":
                    path_status = "retryable"
            paths[path_name] = {"status": path_status, "evidence": evidence}
        finally:
            await context.close()
    status = aggregate_path_status(paths)
    return {"playbackStatus": status, "paths": paths, "checkedAt": utc_now(),
            "sourceUrlHash": candidate.source_url_hash,
            "embedUrlHash": source_url_hash(candidate.embed_url),
            "finalUrl": candidate.embed_url if status == "verified" else None}
