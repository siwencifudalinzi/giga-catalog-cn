"""Run four resumable headless workers; store only bounded playback evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo_streams import JavryoEmbedCandidate  # noqa: E402
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION, utc_now, verify_embed_candidate  # noqa: E402
from src.giga_catalog.javryo_checkpoints import durable_path, load_checkpoint, save_checkpoint  # noqa: E402
from src.giga_catalog.resolved_links import load_json, source_url_hash  # noqa: E402


def candidates_from_file(path: Path) -> list[JavryoEmbedCandidate]:
    rows = load_json(path, {}).get("entries", {})
    return [JavryoEmbedCandidate(
        code=row["code"], page_url=row["pageUrl"], post_id=row["postId"],
        source_url_hash=row["sourceUrlHash"], embed_url=row.get("embedUrl"),
        host=row.get("host", ""), status=row["status"], fetched_at=row["fetchedAt"],
    ) for row in rows.values()]


def progress(results: dict, total: int, added: int) -> str:
    results = {key: row for key, row in results.items()
               if row.get("verificationVersion") == VERIFICATION_VERSION}
    counts = Counter(row.get("playbackStatus") for row in results.values())
    return (f"已处理：{len(results)} / {total}\n确认可播放：{counts['verified']}\n"
            f"播放器与媒体清单可达：{counts['media_reachable']}\n"
            f"验证受阻：{counts['blocked'] + counts['retryable']}\n"
            f"已失效：{counts['dead']}\n不支持：{counts['unsupported']}\n"
            f"本批新增直达：{added}\n当前阶段：后台验证")


def should_queue_candidate(previous: object, source_hash: str, embed_hash: str,
                           *, retry: bool, retry_promising: bool) -> bool:
    if not isinstance(previous, dict) or previous.get("verificationVersion") != VERIFICATION_VERSION:
        return not retry_promising
    if previous.get("sourceUrlHash") != source_hash or previous.get("embedUrlHash") != embed_hash:
        return not retry_promising
    if previous.get("playbackStatus") not in {"retryable", "blocked"}:
        return False
    if not retry or previous.get("attempts", 0) >= 3:
        return False
    if retry_promising:
        paths = previous.get("paths", {})
        direct = paths.get("direct", {}) if isinstance(paths, dict) else {}
        return isinstance(direct, dict) and direct.get("status") in {"verified", "media_reachable"}
    return True


async def run(args) -> None:
    from playwright.async_api import async_playwright

    candidates = candidates_from_file(args.candidates)
    if args.code:
        selected = {code.strip().upper() for code in args.code}
        candidates = [candidate for candidate in candidates if candidate.code in selected]
    state = load_checkpoint(args.state, args.backup_state)
    if not isinstance(state, dict) or not isinstance(state.get("results"), dict):
        state = {"schemaVersion": 1, "results": {}}
    results = state["results"]
    queue = asyncio.Queue()
    for candidate in candidates:
        previous = results.get(candidate.code)
        embed_hash = source_url_hash(candidate.embed_url) if candidate.embed_url else ""
        if not should_queue_candidate(previous, candidate.source_url_hash, embed_hash,
                                      retry=args.retry, retry_promising=args.retry_promising):
            continue
        if args.max_links and queue.qsize() >= args.max_links:
            break
        queue.put_nowait(candidate)

    print(f"queued={queue.qsize()}", flush=True)
    if queue.empty():
        return

    lock = asyncio.Lock()
    added = 0
    async with async_playwright() as manager:
        async def worker():
            nonlocal added
            browser = await manager.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
            try:
                while True:
                    try:
                        candidate = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    previous = results.get(candidate.code, {})
                    if previous.get("verificationVersion") != VERIFICATION_VERSION:
                        previous = {}
                    try:
                        result = await verify_embed_candidate(candidate, browser, timeout_ms=args.timeout_ms)
                    except Exception as error:
                        result = {"playbackStatus": "retryable", "errorCode": type(error).__name__}
                    result.setdefault("checkedAt", utc_now())
                    result["verificationVersion"] = VERIFICATION_VERSION
                    result.setdefault("sourceUrlHash", candidate.source_url_hash)
                    result.setdefault("embedUrlHash", source_url_hash(candidate.embed_url) if candidate.embed_url else "")
                    result["attempts"] = int(previous.get("attempts", 0)) + 1 if isinstance(previous, dict) else 1
                    async with lock:
                        results[candidate.code] = result
                        state["updatedAt"] = utc_now()
                        save_checkpoint(args.state, args.backup_state, state)
                        if result["playbackStatus"] == "verified" and previous.get("playbackStatus") != "verified":
                            added += 1
                        processed = sum(row.get("verificationVersion") == VERIFICATION_VERSION
                                        for row in results.values())
                        if processed % 300 == 0 or processed == len(candidates):
                            print(progress(results, len(candidates), added), flush=True)
                            added = 0
                    queue.task_done()
            finally:
                await browser.close()

        await asyncio.gather(*(worker() for _ in range(4)))
    print(progress(results, len(candidates), added), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=ROOT / "data/javryo-embeds.json")
    parser.add_argument("--state", type=Path, default=ROOT / "data/state/javryo-embed-verification.json")
    parser.add_argument("--backup-state", type=Path)
    parser.add_argument("--max-links", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=12000)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--retry-promising", action="store_true")
    parser.add_argument("--code", action="append", default=[])
    args = parser.parse_args()
    args.backup_state = args.backup_state or durable_path(args.state.name)
    if args.retry_promising and not args.retry:
        parser.error("--retry-promising requires --retry")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
