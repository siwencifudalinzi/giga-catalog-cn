"""Recheck every legacy JAVRyo Streamtape watch with four headless workers."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION, utc_now  # noqa: E402
from src.giga_catalog.javryo_checkpoints import durable_path, load_checkpoint, save_checkpoint  # noqa: E402
from src.giga_catalog.javryo_streamtape_browser import matching_wrapper, verify_streamtape_candidate  # noqa: E402
from src.giga_catalog.resolved_links import load_json, source_url_hash  # noqa: E402


async def run(args):
    from playwright.async_api import async_playwright

    overlay = load_json(ROOT / "data/javryo-links.json", {}).get("entries", {})
    crawl = load_json(ROOT / "data/state/javryo-crawl.json", {}).get("results", {})
    state = load_checkpoint(args.state, args.backup_state)
    results = state["results"]
    queue = asyncio.Queue()
    for code, item in sorted(overlay.items()):
        if item.get("status") != "streamtape_verified":
            continue
        page_url, final_url = item["pageUrl"], item["streamtapeUrl"]
        prior = results.get(code, {})
        if (prior.get("verificationVersion") == VERIFICATION_VERSION
                and prior.get("sourceUrlHash") == source_url_hash(page_url)
                and prior.get("targetUrlHash") == source_url_hash(final_url)
                and (prior.get("playbackStatus") not in {"retryable", "blocked"}
                     or not args.retry or prior.get("attempts", 0) >= 2)):
            continue
        if args.max_links and queue.qsize() >= args.max_links:
            break
        queue.put_nowait((code, page_url, final_url, matching_wrapper(crawl.get(code), final_url)))

    print(f"queued={queue.qsize()}", flush=True)
    if queue.empty():
        return

    lock = asyncio.Lock()
    async with async_playwright() as manager:
        async def worker():
            browser = await manager.chromium.launch(headless=True,
                args=["--autoplay-policy=no-user-gesture-required"])
            try:
                while True:
                    try:
                        code, page_url, final_url, wrapper = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    previous = results.get(code, {})
                    if previous.get("verificationVersion") != VERIFICATION_VERSION:
                        previous = {}
                    try:
                        row = await verify_streamtape_candidate(browser, page_url=page_url,
                            final_url=final_url, wrapper_url=wrapper, timeout_ms=args.timeout_ms)
                    except Exception as error:
                        row = {"playbackStatus": "retryable", "errorCode": type(error).__name__}
                    row.setdefault("checkedAt", utc_now())
                    row["verificationVersion"] = VERIFICATION_VERSION
                    row.setdefault("sourceUrlHash", source_url_hash(page_url))
                    row["targetUrlHash"] = source_url_hash(final_url)
                    row["attempts"] = int(previous.get("attempts", 0)) + 1
                    async with lock:
                        results[code] = row
                        state["updatedAt"] = utc_now()
                        save_checkpoint(args.state, args.backup_state, state)
                        if len(results) % 300 == 0:
                            print(len(results), Counter(v["playbackStatus"] for v in results.values()), flush=True)
                    queue.task_done()
            finally:
                await browser.close()

        await asyncio.gather(*(worker() for _ in range(args.workers)))
    print(len(results), Counter(v["playbackStatus"] for v in results.values()), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "data/state/javryo-streamtape-verification.json")
    parser.add_argument("--backup-state", type=Path)
    parser.add_argument("--max-links", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=4)
    args = parser.parse_args()
    args.backup_state = args.backup_state or durable_path(args.state.name)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
