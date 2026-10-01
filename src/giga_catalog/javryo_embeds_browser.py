"""Headless, bounded playback checks for stable JAVRyo embed pages."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlsplit

from .javryo_streams import JavryoEmbedCandidate, normalize_embed_url
from .resolved_links import source_url_hash, validate_final_url
from .javryo_media_probe import MediaProbeBudget, fetch_probe


VERIFICATION_VERSION = 4
EVENT_SCRIPT = """(() => {
  // Some source pages continuously log objects (140,000 messages in 23s).
  // The driver retains console handles and can exhaust its heap. Console output
  // is not playback evidence; suppress it before page scripts start executing.
  if (window.console) for (const name of ['log','debug','info','warn','error','trace',
      'dir','dirxml','table','assert','group','groupCollapsed','groupEnd','time',
      'timeEnd','timeLog','count','countReset','clear','profile','profileEnd','timeStamp'])
    try { window.console[name] = () => {}; } catch (_) {}
  window.__gigaSourceClickObserved = false;
  window.__gigaLastClickedHref = null;
  document.addEventListener('click', event => {
    const anchor = event.target?.closest?.('a[href]');
    if (event.isTrusted && anchor) window.__gigaLastClickedHref = anchor.href;
    if (event.isTrusted && event.target?.closest?.('#player-option-1'))
      window.__gigaSourceClickObserved = true;
  }, true);
  for (const name of ['playing', 'timeupdate'])
    document.addEventListener(name, event => {
      const video = event.target;
      if (!event.isTrusted || !(video instanceof HTMLVideoElement) || video.paused ||
          (name === 'timeupdate' && !(video.currentTime > 0))) return;
      video.__gigaPlaybackEvents ||= [];
      if (!video.__gigaPlaybackEvents.includes(name)) video.__gigaPlaybackEvents.push(name);
      video.pause();
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
            "embedStatus", "embedFrameSeen", "apiStatus", "clicks", "clickErrors",
            "sourceClickObserved", "trustedVideoEvents", "playerDocumentValidated", "playerFrameStatus",
            "catalogClickObserved", "catalogDocumentValidated")
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
    if (status in (404, 410) or value.get("embedStatus") in (404, 410)
            or value.get("playerFrameStatus") in (404, 410) or value.get("deleted")):
        return "dead"
    if (status in (401, 403, 429) or value.get("embedStatus") in (401, 403, 429)
            or value.get("playerFrameStatus") in (401, 403, 429) or value.get("challenge")
            or value.get("mediaStatus") in (401, 403, 429)
            or value.get("manifestStatus") in (401, 403, 429)):
        return "blocked"
    if status != 200:
        return "retryable"
    if (value.get("videoCount", 0) > 0
            and value.get("trustedVideoEvents") is True
            and value.get("playerDocumentValidated") is True
            and (value.get("manifestStatus") == 200 or value.get("mediaStatus") in (200, 206))
            and any(event in ("playing", "timeupdate") for event in value.get("events", []))):
        return "verified"
    if (value.get("playerDocumentValidated") is True and value.get("videoCount", 0) > 0
            and value.get("duration", 0) > 0 and value.get("manifestStatus") == 200):
        return "media_reachable"
    return "retryable"


def classify_source_observation(value: Mapping) -> str:
    status = classify_observation(value)
    if status in {"dead", "blocked"}:
        return status
    if (value.get("httpStatus") == 200 and value.get("embedStatus") == 200
            and value.get("embedFrameSeen") and value.get("sourceClickObserved")):
        return "reached"
    return "retryable"


def player_document_matches(value: str, expected: str) -> bool:
    def normalize(url):
        return normalize_embed_url(url) or validate_final_url(url, expected_provider="streamtape")
    target = normalize(expected)
    return bool(target and normalize(value) == target)


def internal_player_matches(value: str, expected: str) -> bool:
    """Allow the observed Byse primary frame, never an arbitrary ad frame."""
    target = normalize_embed_url(expected)
    if not target or urlsplit(target).hostname != "bysejikuar.com":
        return False
    parsed = urlsplit(value)
    namespace = parsed.path.split("/")[1] if parsed.path.startswith("/") else ""
    return (parsed.scheme == "https" and parsed.netloc == "n1mwq.org"
            and not parsed.query and not parsed.fragment
            and namespace not in {"ad", "ads", "advert", "promo"}
            and bool(re.fullmatch(r"/[a-z][a-z0-9]{1,7}/" +
                                  re.escape(urlsplit(target).path.rsplit("/", 1)[-1]), parsed.path)))


async def intended_player_frame(page, target_url: str):
    for frame in page.frames:
        if frame.parent_frame != page.main_frame or not internal_player_matches(frame.url, target_url):
            continue
        try:
            element = await frame.frame_element()
            if await element.evaluate("""el => el.matches('.jw8-player-shell > iframe') &&
                el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0"""):
                return frame
        except Exception:
            pass
    return page.main_frame


def remember_document_status(response, context=None):
    """Retain navigation status in memory before a clicked watch is observed."""
    if response.request.resource_type != "document":
        return
    if context is not None:
        statuses_by_url = getattr(context, "_gigaDocumentStatusesByUrl", None)
        if not isinstance(statuses_by_url, dict):
            statuses_by_url = {}
            context._gigaDocumentStatusesByUrl = statuses_by_url
        statuses_by_url[response.url] = response.status
    try:
        frame = response.frame
        page = frame.page
        statuses = getattr(page, "_gigaFrameStatuses", None)
        if not isinstance(statuses, dict):
            statuses = {}
            page._gigaFrameStatuses = statuses
        statuses[frame] = response.status
        if frame == page.main_frame:
            page._gigaDocumentStatus = response.status
    except Exception:
        pass


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


async def _route_media(route, budget):
    request = route.request
    response = await asyncio.to_thread(fetch_probe, {
        "url": request.url, "resourceType": request.resource_type, "method": request.method,
        "headers": await request.all_headers(), "data": request.post_data_buffer,
    }, budget)
    if response is None:
        await route.abort()
    else:
        await route.fulfill(**response)


def bounded_route_handler(*, source_embed_host: str = ""):
    budget = MediaProbeBudget(allow_media=not bool(source_embed_host))

    async def handle(route):
        request = route.request
        if (request.resource_type in {"image", "font"} or AD_HOST_RE.search(request.url)
                or (source_embed_host and not source_request_allowed(request.url, source_embed_host))):
            await route.abort()
        elif request.resource_type in {"media", "fetch", "xhr", "document"} or MEDIA_EXT_RE.search(urlsplit(request.url).path):
            await _route_media(route, budget)
        else:
            await route.continue_()
    return handle


async def _observe(page, target_url: str, *, source_page: bool, timeout_ms: int,
                   navigate: bool = True, expected_embed: str = "") -> dict:
    observation = {"httpStatus": None, "videoCount": 0, "duration": 0, "events": []}
    media_requests = 0
    existing_statuses = getattr(page, "_gigaFrameStatuses", None)
    document_statuses = dict(existing_statuses) if isinstance(existing_statuses, dict) else {}
    cached_urls = getattr(page.context, "_gigaDocumentStatusesByUrl", None)
    cached_urls = cached_urls if isinstance(cached_urls, dict) else {}

    async def on_response(response):
        nonlocal media_requests
        path = urlsplit(response.url).path.lower()
        content_type = response.headers.get("content-type", "").lower()
        if not source_page and response.request.resource_type == "document":
            document_statuses[response.frame] = response.status
        if source_page and "/wp-json/dooplayer/v1/post/" in urlsplit(response.url).path:
            observation["apiStatus"] = response.status
        if (expected_embed and normalize_embed_url(response.url) == expected_embed
                and response.request.resource_type == "document"):
            observation["embedStatus"] = response.status
        if source_page or not player_document_matches(page.url, target_url):
            return
        if response.frame != await intended_player_frame(page, target_url):
            return
        if path.endswith(".m3u8") or "mpegurl" in content_type:
            observation["manifestStatus"] = response.status
            observation["manifestHost"] = (urlsplit(response.url).hostname or "").lower()
        elif (MEDIA_EXT_RE.search(path) or response.request.resource_type == "media"
              or response.headers.get("x-giga-bounded-probe") == "media") and media_requests < 2:
            observation["mediaStatus"] = response.status
            media_requests += 1

    page.on("response", on_response)
    try:
        if navigate:
            response = await page.goto(target_url, wait_until="commit", timeout=timeout_ms)
            observation["httpStatus"] = response.status if response else None
        else:
            cached_status = getattr(page, "_gigaDocumentStatus", None)
            if not isinstance(cached_status, int):
                cached_status = cached_urls.get(page.url)
            observation["httpStatus"] = cached_status if isinstance(cached_status, int) and page.url == target_url else None
        if observation["httpStatus"] != 200:
            return safe_evidence(observation)
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
            text = await page.evaluate("() => (document.body?.innerText || '').slice(0, 500)")
            if CHALLENGE_RE.search(text):
                observation["challenge"] = True
            if DELETED_RE.search(text):
                observation["deleted"] = True
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        source_retry_at = asyncio.get_running_loop().time() + 5
        source_retried = False
        clicked = set()
        while asyncio.get_running_loop().time() < deadline:
            if source_page:
                observation["sourceClickObserved"] = bool(await page.evaluate(
                    "() => window.__gigaSourceClickObserved === true"))
                observation["embedFrameSeen"] = any(normalize_embed_url(frame.url) == expected_embed
                                                   for frame in page.frames)
                if classify_source_observation(observation) in {"reached", "blocked", "dead"}:
                    break
            if (source_page and not source_retried and not observation.get("sourceClickObserved")
                    and asyncio.get_running_loop().time() >= source_retry_at):
                source_retried = True
                try:
                    await page.locator("#player-option-1").click(timeout=3000, no_wait_after=True)
                except Exception:
                    pass
            if source_page:
                await page.wait_for_timeout(500)
                continue
            if not player_document_matches(page.url, target_url):
                observation["errorCode"] = "unexpected-player-destination"
                break
            player_frame = await intended_player_frame(page, target_url)
            frame_status = document_statuses.get(player_frame)
            if frame_status is None:
                frame_status = cached_urls.get(player_frame.url)
            if player_frame == page.main_frame and frame_status is None:
                frame_status = observation["httpStatus"]
            observation["playerFrameStatus"] = frame_status
            observation["playerDocumentValidated"] = frame_status == 200
            for frame in [player_frame]:
                try:
                    frame_data = await frame.evaluate("""() => {
                      const videos = [...document.querySelectorAll('video')];
                      const selected = videos.find(v => v.closest('.jwplayer,.video-js,.plyr,#player')) ||
                        (videos.length === 1 ? videos[0] : null);
                      return {events: selected?.__gigaPlaybackEvents || [], count: selected ? 1 : 0,
                        duration: Number.isFinite(selected?.duration) ? selected.duration : 0,
                        text: (document.body?.innerText || '').slice(0, 500)};
                    }""")
                except Exception:
                    continue
                observation["videoCount"] = max(observation["videoCount"], frame_data["count"])
                observation["duration"] = max(observation["duration"], frame_data["duration"])
                observation["events"] = list(set(observation["events"] + frame_data["events"]))
                observation["trustedVideoEvents"] = bool(frame_data["events"])
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
            if classify_observation(observation) == "verified" or observation.get("deleted") or observation.get("challenge"):
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
        await context.route("**/*", bounded_route_handler(
            source_embed_host=candidate.host if path_name == "source" else ""))
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
            path_status = classify_source_observation(evidence) if path_name == "source" else classify_observation(evidence)
            paths[path_name] = {"status": path_status, "evidence": evidence}
        finally:
            await context.close()
    status = aggregate_path_status(paths)
    return {"playbackStatus": status, "paths": paths, "checkedAt": utc_now(),
            "verificationVersion": VERIFICATION_VERSION,
            "sourceUrlHash": candidate.source_url_hash,
            "embedUrlHash": source_url_hash(candidate.embed_url),
            "finalUrl": candidate.embed_url if status == "verified" else None}
