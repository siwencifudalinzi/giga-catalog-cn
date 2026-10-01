"""Verify legacy Streamtape watches through both JAVRyo navigation and direct access."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit
from typing import Optional

from .javryo_embeds_browser import (
    EVENT_SCRIPT, _observe, aggregate_path_status, bounded_route_handler,
    classify_observation, safe_evidence, utc_now, remember_document_status,
)
from .resolved_links import source_url_hash, validate_final_url


def matching_wrapper(crawl_row: dict, target: str):
    for item in crawl_row.get("targets", []) if isinstance(crawl_row, dict) else []:
        wrapper = item.get("wrapperUrl") if isinstance(item, dict) else None
        if (isinstance(item, dict) and item.get("target") == target and isinstance(wrapper, str)
                and re.fullmatch(r"https://javryo\.com/links/[a-z0-9]+/", wrapper)):
            return wrapper
    return None


async def _context(browser):
    context = await browser.new_context(accept_downloads=False, service_workers="block")
    context.on("response", remember_document_status)
    await context.add_init_script(EVENT_SCRIPT)
    await context.route("**/*", bounded_route_handler())
    return context


async def _clicked_path(browser, page_url: str, wrapper: str, final_url: str,
                        timeout_ms: int) -> dict:
    context = await _context(browser)
    try:
        source = await context.new_page()
        response = await source.goto(page_url, wait_until="commit", timeout=timeout_ms)
        if not response or response.status != 200:
            return {"status": "blocked" if response and response.status == 403 else "retryable",
                    "evidence": {"httpStatus": response.status if response else None}}
        try:
            await source.locator(f'a[href="{wrapper}"]').first.click(timeout=8000,
                no_wait_after=True, force=True)
            await source.wait_for_timeout(1500)
        except Exception:
            return {"status": "retryable", "evidence": {"errorCode": "wrapper-click"}}
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        clicked_link = False
        while asyncio.get_running_loop().time() < deadline:
            pages = list(context.pages)
            reached = next((page for page in pages if page.url == final_url), None)
            if reached:
                evidence = await _observe(reached, final_url, source_page=False,
                                          timeout_ms=timeout_ms, navigate=False)
                return {"status": classify_observation(evidence), "evidence": evidence}
            if not clicked_link:
                wrapper_page = next((page for page in pages if page.url == wrapper), None)
                if wrapper_page:
                    link = wrapper_page.locator("#link")
                    try:
                        if await link.count() and await link.is_visible():
                            await link.click(timeout=2000, no_wait_after=True)
                            clicked_link = True
                    except Exception:
                        pass
            else:
                for page in pages:
                    if (urlsplit(page.url).hostname or "") in {"ouo.io", "www.ouo.io", "ouo.press"}:
                        for name in ("I'm a human", "Get Link"):
                            button = page.get_by_role("button", name=name)
                            try:
                                if await button.count() and await button.is_visible():
                                    await button.click(timeout=1000, no_wait_after=True)
                            except Exception:
                                pass
            await source.wait_for_timeout(500)
        return {"status": "blocked" if clicked_link else "retryable",
                "evidence": {"errorCode": "source-flow-blocked" if clicked_link else "wrapper-timeout"}}
    except Exception as error:
        return {"status": "retryable", "evidence": {"errorCode": type(error).__name__}}
    finally:
        await context.close()


async def verify_streamtape_candidate(browser, *, page_url: str, final_url: str,
                                      wrapper_url: Optional[str], timeout_ms: int = 20000) -> dict:
    if validate_final_url(final_url, expected_provider="streamtape") != final_url:
        return {"playbackStatus": "unsupported"}
    if wrapper_url:
        source = await _clicked_path(browser, page_url, wrapper_url, final_url, timeout_ms)
    else:
        context = await _context(browser)
        try:
            page = await context.new_page()
            response = await page.goto(page_url, wait_until="commit", timeout=timeout_ms)
            status = response.status if response else None
            source = {"status": "blocked" if status in (401, 403, 429) else "retryable",
                      "evidence": {"httpStatus": status, "errorCode": "missing-wrapper"}}
        except Exception as error:
            source = {"status": "retryable", "evidence": {"errorCode": type(error).__name__}}
        finally:
            await context.close()
    context = await _context(browser)
    try:
        direct_page = await context.new_page()
        evidence = await _observe(direct_page, final_url, source_page=False, timeout_ms=timeout_ms)
        direct = {"status": classify_observation(evidence), "evidence": safe_evidence(evidence)}
    finally:
        await context.close()
    paths = {"source": source, "direct": direct}
    status = aggregate_path_status(paths)
    return {"playbackStatus": status, "paths": paths, "checkedAt": utc_now(),
            "sourceUrlHash": source_url_hash(page_url),
            "finalUrl": final_url if status == "verified" else None}
