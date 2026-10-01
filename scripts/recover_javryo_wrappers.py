"""Recover the 232 legacy source navigation paths using bounded metadata requests."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.crawl_javryo import _get_text
from src.giga_catalog.javryo import extract_movie_page, extract_wrapper_target
from src.giga_catalog.javryo_embeds_browser import utc_now
from src.giga_catalog.javryo_checkpoints import durable_path, load_checkpoint, save_checkpoint
from src.giga_catalog.javryo_streamtape_browser import matching_wrapper
from src.giga_catalog.resolved_links import load_json, source_url_hash


def recover(code, item):
    row = {"code": code, "pageUrl": item["pageUrl"], "targets": [], "status": "retryable",
           "sourceUrlHash": source_url_hash(item["pageUrl"]),
           "targetUrlHash": source_url_hash(item["streamtapeUrl"]), "checkedAt": utc_now()}
    status, body = _get_text(item["pageUrl"], 12)
    row["pageStatus"] = status
    if status != 200:
        return row
    metadata = extract_movie_page(body)
    row["postId"] = metadata["postId"]
    for wrapper in metadata["wrapperUrls"]:
        status, body = _get_text(wrapper, 12)
        if status == 200 and extract_wrapper_target(body) == item["streamtapeUrl"]:
            row["targets"] = [{"wrapperUrl": wrapper, "target": item["streamtapeUrl"], "status": 200}]
            row["status"] = "found"
            break
    return row


def main():
    primary = ROOT / "data/state/javryo-crawl.json"
    backup = durable_path(primary.name)
    state = load_checkpoint(primary, backup)
    results = state["results"]
    overlay = load_json(ROOT / "data/javryo-links.json", {})["entries"]
    targets = [(code, item) for code, item in sorted(overlay.items())
               if item.get("status") == "streamtape_verified"]
    pending = [(code, item) for code, item in targets
               if not matching_wrapper(results.get(code), item["streamtapeUrl"])]
    print(f"legacy={len(targets)} pending={len(pending)}", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(recover, code, item): code for code, item in pending}
        for future in as_completed(futures):
            code = futures[future]
            try:
                row = future.result()
            except Exception as error:
                row = {"code": code, "status": "retryable", "errorCode": type(error).__name__}
            results[code] = row
            state["updatedAt"] = utc_now()
            save_checkpoint(primary, backup, state)
            if len(results) % 50 == 0:
                print(f"recovered={len(results)} found={sum(r.get('status') == 'found' for r in results.values())}", flush=True)
    print(f"recovered={len(results)} found={sum(r.get('status') == 'found' for r in results.values())}", flush=True)


if __name__ == "__main__":
    main()
