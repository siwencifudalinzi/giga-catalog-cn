"""Check every published GIGA button and fresh direct player in headless browsers."""

import argparse
import asyncio
import copy
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo_checkpoints import durable_path, load_checkpoint, save_checkpoint
from src.giga_catalog.javryo_embeds_browser import (
    EVENT_SCRIPT, VERIFICATION_VERSION, _observe, bounded_route_handler,
    classify_observation, remember_document_status, utc_now,
)
from src.giga_catalog.resolved_links import load_json, source_url_hash, validate_final_url

SITE = "https://siwencifudalinzi.github.io/giga-catalog-cn/"


def javryo_entries(manifest):
    return {code: slots["standard.javryo"] for code, slots in manifest["entries"].items()
            if "standard.javryo" in slots}


def reusable_path(row, name, url, generation, *, now=None):
    if (row.get("generation") != generation or row.get("targetUrlHash") != source_url_hash(url)
            or row.get("verificationVersion") != VERIFICATION_VERSION):
        return False
    path = row.get("paths", {}).get(name, {})
    evidence = path.get("evidence", {})
    if path.get("status") != "verified" or classify_observation(evidence) != "verified":
        return False
    if name == "catalog" and (evidence.get("catalogClickObserved") is not True
                              or evidence.get("catalogDocumentValidated") is not True):
        return False
    try:
        checked = datetime.fromisoformat(path.get("checkedAt", row.get("checkedAt", "")).replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return False
    return 0 <= age < 600


def catalog_asset(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "siwencifudalinzi.github.io":
        return None
    prefix = "/giga-catalog-cn/"
    if not parsed.path.startswith(prefix):
        return None
    relative = unquote(parsed.path[len(prefix):]) or "index.html"
    path = (ROOT / "public" / relative).resolve()
    public = (ROOT / "public").resolve()
    if public not in path.parents or path.suffix not in {".html", ".json", ".js", ".css", ".svg"}:
        return None
    return path if path.is_file() else None


async def context_for(browser, preview, manifest_override=None):
    context = await browser.new_context(accept_downloads=False, service_workers="block",
                                        extra_http_headers={"Cache-Control": "no-cache"})
    await context.add_init_script(EVENT_SCRIPT)
    context.on("response", lambda response: remember_document_status(response, context))
    bounded = bounded_route_handler()

    async def route_request(route):
        request = route.request
        asset = catalog_asset(request.url)
        if asset and request.resource_type not in {"image", "font", "media"}:
            if preview:
                body = (json.dumps(manifest_override).encode("utf-8")
                        if manifest_override is not None and asset == ROOT / "public/data/resolved-links.json"
                        else asset.read_bytes())
                await route.fulfill(body=body,
                    content_type=mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            else:
                # Only the site's own controlled metadata/assets bypass the player byte budget.
                await route.continue_()
        else:
            await bounded(route)

    await context.route("**/*", route_request)
    return context


async def check_path(browser, code, url, *, catalog, preview, timeout_ms, manifest_override=None):
    context = await context_for(browser, preview, manifest_override)
    try:
        page = await context.new_page()
        if catalog:
            response = await page.goto(SITE, wait_until="domcontentloaded", timeout=30000)
            source_validated = bool(response and response.status == 200
                                    and urlsplit(page.url).scheme == "https"
                                    and urlsplit(page.url).netloc == "siwencifudalinzi.github.io"
                                    and urlsplit(page.url).path == "/giga-catalog-cn/")
            await page.locator("#catalog-search").fill(code)
            await page.locator(f'[data-action="open-video"][data-code="{code}"]').first.click(timeout=30000)
            link = page.locator(f'#video-detail[open] a[href="{url}"]').first
            await link.wait_for(state="visible", timeout=30000)
            async with context.expect_page(timeout=15000) as opened:
                await link.click(timeout=10000, no_wait_after=True)
            player = await opened.value
            clicked = await page.evaluate("url => window.__gigaLastClickedHref === url", url)
            await player.wait_for_load_state("domcontentloaded", timeout=20000)
            evidence = await _observe(player, url, source_page=False,
                                      timeout_ms=timeout_ms, navigate=False)
            evidence.update(catalogClickObserved=bool(clicked), catalogDocumentValidated=source_validated)
            if not clicked or not source_validated:
                return {"status": "retryable", "evidence": evidence}
        else:
            evidence = await _observe(page, url, source_page=False, timeout_ms=timeout_ms)
        return {"status": classify_observation(evidence), "evidence": evidence}
    except Exception as error:
        return {"status": "retryable", "evidence": {"errorCode": type(error).__name__}}
    finally:
        await context.close()


async def run(args):
    from playwright.async_api import async_playwright

    bootstrap = load_json(ROOT / "public/data/catalog-bootstrap.json", {})
    local = javryo_entries(load_json(ROOT / "public/data/resolved-links.json", {}))
    manifest_override = None
    if args.preview_promising:
        inventory = load_json(ROOT / "data/javryo-embeds.json", {})["entries"]
        overlay = load_json(ROOT / "data/javryo-links.json", {})["entries"]
        candidates = {}
        for code, row in load_json(ROOT / "data/state/javryo-embed-verification.json", {})["results"].items():
            item = inventory.get(code, {})
            url = validate_final_url(item.get("embedUrl"), expected_provider="javryo_stream")
            direct = row.get("paths", {}).get("direct", {})
            if (not url or code not in overlay or row.get("verificationVersion") != VERIFICATION_VERSION
                    or row.get("sourceUrlHash") != source_url_hash(overlay[code]["pageUrl"])
                    or row.get("embedUrlHash") != source_url_hash(url)
                    or direct.get("status") != "verified"
                    or classify_observation(direct.get("evidence", {})) != "verified"):
                continue
            candidates[code] = {"provider": "javryo_stream", "sourceUrlHash": row["sourceUrlHash"],
                "finalUrl": url, "kind": "external", "status": "verified", "playbackStatus": "verified",
                "checkedAt": row["checkedAt"]}
        local = candidates
        # Provisional entries exist only in intercepted headless preview responses.
        # They cannot be deployed; promotion requires both fresh real-playback paths.
        manifest_override = load_json(ROOT / "public/data/resolved-links.json", {})
        manifest_override["entries"] = {code: {slot: entry for slot, entry in slots.items()
                                       if slot != "standard.javryo"}
                                       for code, slots in manifest_override["entries"].items()}
        for code, entry in candidates.items():
            manifest_override["entries"].setdefault(code, {})["standard.javryo"] = entry
    if args.code:
        selected = {code.upper() for code in args.code}
        local = {code: entry for code, entry in local.items() if code in selected}
    if not args.preview_local:
        def remote_json(path):
            response = requests.get(SITE + "data/" + path, headers={"Cache-Control": "no-cache"},
                                    params={"check": utc_now()}, timeout=(5, 30))
            response.raise_for_status()
            return response.json()
        remote_bootstrap = await asyncio.to_thread(remote_json, "catalog-bootstrap.json")
        remote_manifest = await asyncio.to_thread(remote_json, "resolved-links.json")
        assert remote_bootstrap["generation"] == bootstrap["generation"], "online generation mismatch"
        assert javryo_entries(remote_manifest) == local, "online direct entries mismatch"
    state = load_checkpoint(args.state, args.backup_state)
    results = state["results"]
    queue = asyncio.Queue()
    for code, entry in sorted(local.items()):
        url = validate_final_url(entry["finalUrl"], expected_provider=entry["provider"])
        assert url == entry["finalUrl"] and entry["playbackStatus"] == "verified"
        old = results.get(code, {})
        if (not args.fresh and old.get("generation") == bootstrap["generation"]
                and old.get("targetUrlHash") == source_url_hash(url)
                and old.get("verificationVersion") == VERIFICATION_VERSION
                and old.get("playbackStatus") == "verified"
                and old.get("paths", {}).get("catalog", {}).get("evidence", {}).get("catalogClickObserved") is True
                and old.get("paths", {}).get("catalog", {}).get("evidence", {}).get("catalogDocumentValidated") is True):
            continue
        queue.put_nowait((code, url))
    lock = asyncio.Lock()
    async with async_playwright() as manager:
        async def worker():
            browser = await manager.chromium.launch(headless=True,
                args=["--autoplay-policy=no-user-gesture-required"])
            try:
                while not queue.empty():
                    code, url = queue.get_nowait()
                    previous = results.get(code, {})
                    paths = {}
                    for name in ("catalog", "direct"):
                        if not args.fresh and reusable_path(previous, name, url, bootstrap["generation"]):
                            paths[name] = copy.deepcopy(previous["paths"][name])
                            paths[name].setdefault("checkedAt", previous["checkedAt"])
                        else:
                            paths[name] = await check_path(browser, code, url,
                                catalog=name == "catalog", preview=args.preview_local, timeout_ms=args.timeout_ms,
                                manifest_override=manifest_override)
                            paths[name]["checkedAt"] = utc_now()
                    row = {"paths": paths, "generation": bootstrap["generation"],
                           "targetUrlHash": source_url_hash(url), "checkedAt": utc_now(),
                           "verificationVersion": VERIFICATION_VERSION,
                           "playbackStatus": "verified" if all(p["status"] == "verified"
                               for p in paths.values()) else "retryable"}
                    async with lock:
                        results[code] = row
                        state["updatedAt"] = utc_now()
                        save_checkpoint(args.state, args.backup_state, state)
                        print(code, {name: path["status"] for name, path in paths.items()}, flush=True)
                    queue.task_done()
            finally:
                await browser.close()
        await asyncio.gather(*(worker() for _ in range(min(4, queue.qsize()))))
    failed = [code for code in local if results.get(code, {}).get("playbackStatus") != "verified"]
    print(json.dumps({"generation": bootstrap["generation"], "published": len(local),
                      "bothPathsVerified": len(local) - len(failed), "failedCodes": failed,
                      "preview": args.preview_local}), flush=True)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-local", action="store_true")
    parser.add_argument("--preview-promising", action="store_true")
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--state", type=Path)
    parser.add_argument("--backup-state", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.preview_promising:
        args.preview_local = True
    args.state = args.state or ROOT / "data/state" / ("javryo-promising-verification.json" if args.preview_promising
                                                   else "javryo-preview-verification.json"
                                                   if args.preview_local else "javryo-live-verification.json")
    args.backup_state = args.backup_state or durable_path(args.state.name)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
