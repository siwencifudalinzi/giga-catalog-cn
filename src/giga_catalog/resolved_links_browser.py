"""Recover public landing pages with a persistent, user-visible Chrome session."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, MutableMapping
from urllib.parse import urlsplit

from .resolved_links import LinkCandidate, validate_final_url

OUO_FLOW_HOSTS = {"ouo.io", "www.ouo.io", "ouo.press", "www.ouo.press"}


def is_ouo_flow_url(value: str) -> bool:
    return (urlsplit(value).hostname or "").lower() in OUO_FLOW_HOSTS


def choose_flow_url(urls):
    for value in urls:
        if validate_final_url(value):
            return value
    for value in urls:
        if is_ouo_flow_url(value):
            return value
    return None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def collect_candidates(
    candidates: Iterable[LinkCandidate],
    state: MutableMapping[str, object],
    resolver: Callable[[LinkCandidate], Awaitable[Mapping[str, object]]],
    *,
    checkpoint: Callable[[MutableMapping[str, object]], None],
    max_links: int = 0,
    delay_seconds: float = 0,
) -> int:
    results = state.setdefault("results", {})
    if not isinstance(results, dict):
        results = {}
        state["results"] = results
    processed = 0
    for candidate in candidates:
        previous = results.get(candidate.key)
        if _should_skip(candidate, previous):
            continue
        if max_links and processed >= max_links:
            break
        raw = await _resolve_safely(resolver, candidate)
        result = _make_result(candidate, previous, raw)
        results[candidate.key] = result
        state["schemaVersion"] = 1
        state["updatedAt"] = result["checkedAt"]
        checkpoint(state)
        processed += 1
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
    return processed


def _should_skip(candidate, previous):
    return (
        isinstance(previous, Mapping)
        and previous.get("sourceUrlHash") == candidate.source_url_hash
        and previous.get("status") != "retryable"
    )


async def _resolve_safely(resolver, candidate):
    try:
        return dict(await resolver(candidate))
    except Exception as error:  # persist only the exception class
        return {"status": "retryable", "errorCode": type(error).__name__}


def _make_result(candidate, previous, raw):
    attempts = int(previous.get("attempts", 0)) + 1 if isinstance(previous, Mapping) else 1
    final_url = validate_final_url(raw.get("finalUrl"))
    status = raw.get("status")
    if status == "verified" and not final_url:
        status = "retryable"
    result = {
        "sourceUrlHash": candidate.source_url_hash,
        "status": status if status in {"verified", "blocked-human", "retryable", "unsupported", "dead"} else "retryable",
        "checkedAt": utc_now(),
        "attempts": attempts,
    }
    if result["status"] == "verified":
        result["finalUrl"] = final_url
    elif isinstance(raw.get("errorCode"), str):
        result["errorCode"] = raw["errorCode"][:80]
    observed_host = raw.get("observedHost")
    if (
        result["status"] != "verified"
        and isinstance(observed_host, str)
        and len(observed_host) <= 253
        and all(part and part.replace("-", "").isalnum() for part in observed_host.split("."))
    ):
        result["observedHost"] = observed_host.lower()
    return result


async def collect_candidates_parallel(
    candidates: Iterable[LinkCandidate],
    state: MutableMapping[str, object],
    resolvers,
    *,
    checkpoint: Callable[[MutableMapping[str, object]], None],
    max_links: int = 0,
    delay_seconds: float = 0,
) -> int:
    results = state.setdefault("results", {})
    if not isinstance(results, dict):
        results = {}
        state["results"] = results
    pending = [candidate for candidate in candidates if not _should_skip(candidate, results.get(candidate.key))]
    if max_links:
        pending = pending[:max_links]
    queue = asyncio.Queue()
    for candidate in pending:
        queue.put_nowait(candidate)
    lock = asyncio.Lock()

    async def worker(resolver):
        while True:
            try:
                candidate = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            raw = await _resolve_safely(resolver, candidate)
            async with lock:
                previous = results.get(candidate.key)
                result = _make_result(candidate, previous, raw)
                results[candidate.key] = result
                state["schemaVersion"] = 1
                state["updatedAt"] = result["checkedAt"]
                checkpoint(state)
            queue.task_done()
            if delay_seconds:
                await asyncio.sleep(delay_seconds)

    await asyncio.gather(*(worker(resolver) for resolver in resolvers))
    return len(pending)


class PlaywrightOuoResolver:
    """Execute ouo's normal two-button flow and return only an allowlisted landing page."""

    def __init__(self, context, *, timeout_ms: int = 45_000):
        self.context = context
        self.timeout_ms = timeout_ms
        self.page = context.pages[0] if context.pages else None

    @classmethod
    async def launch(cls, profile_dir: Path, *, headless: bool = False, timeout_ms: int = 45_000):
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError("Install browser dependencies with: py -m pip install -r requirements-browser.txt") from error
        manager = await async_playwright().start()
        context = await manager.chromium.launch_persistent_context(
            str(profile_dir),
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 900},
            accept_downloads=False,
        )
        instance = cls(context, timeout_ms=timeout_ms)
        instance._manager = manager
        return instance

    async def close(self):
        await self.context.close()
        manager = getattr(self, "_manager", None)
        if manager:
            await manager.stop()

    async def _main_page(self):
        if self.page is None or self.page.is_closed():
            self.page = await self.context.new_page()
        for extra in list(self.context.pages):
            if extra is not self.page:
                try:
                    await extra.close()
                except Exception:
                    pass
        return self.page

    async def __call__(self, candidate: LinkCandidate) -> Mapping[str, object]:
        page = await self._main_page()
        try:
            response = await page.goto(
                candidate.source_url,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        except Exception:
            response = None
        await page.wait_for_timeout(10_000)
        for _ in range(8):
            page = await self._adopt_flow_page()
            final_url = validate_final_url(page.url)
            if final_url:
                return {"status": "verified", "finalUrl": final_url}
            if not is_ouo_flow_url(page.url):
                return {
                    "status": "retryable",
                    "errorCode": "unknown-destination",
                    "observedHost": (urlsplit(page.url).hostname or "").lower(),
                }
            title = (await page.title()).lower()
            if "just a moment" in title or (response is not None and response.status == 403):
                await page.wait_for_timeout(5_000)
                response = None
                continue
            human = page.get_by_role("button", name="I'm a human")
            if await human.count() and await human.first.is_visible():
                try:
                    await human.first.click(timeout=5_000, no_wait_after=True)
                except Exception:
                    pass
                await page.wait_for_timeout(2_500)
                await self._adopt_flow_page()
                continue
            get_link = page.get_by_role("button", name="Get Link")
            if await get_link.count() and await get_link.first.is_visible():
                await page.wait_for_timeout(5_500)
                try:
                    await get_link.first.click(timeout=5_000, no_wait_after=True)
                except Exception:
                    pass
                await page.wait_for_timeout(5_000)
                await self._adopt_flow_page()
                continue
            if "verify" in title or "captcha" in title or "security" in title:
                return {"status": "blocked-human", "errorCode": "human-verification"}
            await page.wait_for_timeout(2_000)
        final_url = validate_final_url(page.url)
        if final_url:
            return {"status": "verified", "finalUrl": final_url}
        return {"status": "retryable", "errorCode": "flow-timeout"}

    async def _adopt_flow_page(self):
        pages = [page for page in self.context.pages if not page.is_closed()]
        selected_url = choose_flow_url([page.url for page in pages])
        selected = next((page for page in pages if page.url == selected_url), self.page)
        if selected is not None:
            self.page = selected
        for extra in pages:
            if extra is self.page:
                continue
            try:
                await extra.close()
            except Exception:
                pass
        return self.page
