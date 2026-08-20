"""Backfill and incrementally refresh official GIGA product tags."""

from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading
import time
from typing import Iterable, Mapping, Optional, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.merge import build_catalog, serialize_catalog
from src.giga_catalog.scraper import (
    BASE_URL,
    PRODUCT_URL,
    create_session,
    parse_product_page,
)
from src.giga_catalog.tags import (
    normalize_tag_definitions,
    parse_product_tags,
    parse_tag_directory,
)
from src.giga_catalog.validation import validate_catalog
from scripts.refresh import _commit_transaction


TAG_DIRECTORY_URLS = {
    "genre": f"{BASE_URL}/search/tag.php?mode=1",
    "character": f"{BASE_URL}/search/tag.php?mode=2",
}
TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
_THREAD_STATE = threading.local()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "public" / "data" / "catalog.json",
    )
    parser.add_argument(
        "--products",
        type=Path,
        default=ROOT / "data" / "raw" / "products.json",
    )
    parser.add_argument(
        "--tags",
        type=Path,
        default=ROOT / "data" / "raw" / "tags.json",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=ROOT / "data" / "tag-translation-overrides.json",
    )
    parser.add_argument(
        "--product-id-overrides",
        type=Path,
        default=ROOT / "data" / "product-id-overrides.json",
        help="Reviewed code-to-productId recovery map for official archive entries",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=ROOT / "data" / "state" / "tag-sync-checkpoint.json",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--stale-days", type=int, default=90)
    parser.add_argument("--max-products", type=int)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def apply_product_detail(
    existing: Mapping[str, object],
    detail: Mapping[str, object],
    tags: Sequence[Mapping[str, object]],
    timestamp: str,
) -> dict:
    """Overlay one verified detail response without dropping private link fields."""
    existing_code = str(existing.get("code") or "")
    if detail.get("code") != existing_code:
        raise ValueError(
            f"product detail code mismatch: {existing_code} != {detail.get('code')}"
        )
    enriched = copy.deepcopy(dict(existing))
    for key in (
        "productId",
        "code",
        "number",
        "title",
        "actors",
        "releaseDate",
        "cover",
        "previewBase",
        "previewCount",
    ):
        if key in detail:
            enriched[key] = copy.deepcopy(detail[key])
    enriched.pop("series", None)
    enriched["tagIds"] = sorted(
        {
            tag["id"]
            for tag in tags
            if isinstance(tag, Mapping)
            and isinstance(tag.get("id"), int)
            and not isinstance(tag.get("id"), bool)
            and tag["id"] > 0
        }
    )
    enriched["tagsStatus"] = "complete"
    enriched["tagsUpdatedAt"] = timestamp
    enriched["tagsSource"] = "official"
    return enriched


def apply_product_id_overrides(
    records: Sequence[Mapping[str, object]],
    overrides: Mapping[str, object],
) -> list:
    """Apply reviewed official product IDs and reject conflicting mappings."""
    enriched = []
    for source in records:
        record = copy.deepcopy(dict(source))
        code = str(record.get("code") or "")
        override = overrides.get(code)
        if override is not None:
            if not _positive_int(override):
                raise ValueError(f"invalid productId override for {code}: {override!r}")
            existing = record.get("productId")
            if _positive_int(existing) and existing != override:
                raise ValueError(
                    f"productId override conflict for {code}: {existing} != {override}"
                )
            record["productId"] = int(override)
        enriched.append(record)
    unknown = sorted(set(overrides) - {str(item.get("code") or "") for item in records})
    if unknown:
        raise ValueError("productId overrides reference unknown codes: " + ", ".join(unknown))
    return enriched


def mark_unavailable_product_tags(
    source: Mapping[str, object], timestamp: str
) -> dict:
    """Resolve a retained official archive record whose detail page is unavailable."""
    record = copy.deepcopy(dict(source))
    record["tagIds"] = []
    record["tagsStatus"] = "complete"
    record["tagsSource"] = "official-unavailable"
    if not _optional_timestamp(record.get("tagsUpdatedAt")):
        record["tagsUpdatedAt"] = timestamp
    return record


def merge_tag_definitions(
    directory: Sequence[Mapping[str, object]],
    stored: Sequence[Mapping[str, object]],
) -> list:
    """Keep live directory metadata plus referenced detail-only definitions."""
    by_id = {
        int(tag["id"]): copy.deepcopy(dict(tag))
        for tag in stored
        if _positive_int(tag.get("id"))
    }
    for tag in directory:
        if _positive_int(tag.get("id")):
            by_id[int(tag["id"])] = copy.deepcopy(dict(tag))
    return [by_id[tag_id] for tag_id in sorted(by_id)]


def select_tag_sync_targets(
    records: Sequence[Mapping[str, object]],
    *,
    now: str,
    stale_days: int,
    full: bool = False,
    max_products: Optional[int] = None,
) -> list:
    """Select missing/failed records first, then the oldest stale records."""
    now_value = _parse_timestamp(now)
    stale_before = now_value - timedelta(days=max(stale_days, 0))
    urgent = []
    stale = []
    for record in records:
        if not _positive_int(record.get("productId")):
            continue
        if full:
            urgent.append(record)
            continue
        if record.get("tagsStatus") != "complete":
            urgent.append(record)
            continue
        updated = _optional_timestamp(record.get("tagsUpdatedAt"))
        if updated is None or updated < stale_before:
            stale.append(record)
    urgent.sort(key=lambda item: (int(item["productId"]), str(item.get("code") or "")))
    stale.sort(
        key=lambda item: (
            str(item.get("tagsUpdatedAt") or ""),
            int(item["productId"]),
        )
    )
    selected = urgent + stale
    if max_products is not None:
        selected = selected[: max(max_products, 0)]
    return selected


def run_sync(argv: Optional[Sequence[str]] = None) -> dict:
    options = create_parser().parse_args(list(argv) if argv is not None else None)
    if options.workers <= 0 or options.retries <= 0 or options.timeout <= 0:
        raise ValueError("workers, retries, and timeout must be positive")
    catalog = _read_json(options.catalog)
    records = _flatten_catalog(catalog)
    product_id_overrides = (
        _read_json(options.product_id_overrides)
        if options.product_id_overrides.is_file()
        else {}
    )
    if not isinstance(product_id_overrides, Mapping):
        raise ValueError("productId overrides must be an object")
    records = apply_product_id_overrides(records, product_id_overrides)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    targets = select_tag_sync_targets(
        records,
        now=stamp,
        stale_days=options.stale_days,
        full=options.full,
        max_products=options.max_products,
    )
    checkpoint = _load_checkpoint(options.checkpoint)

    definitions = _fetch_tag_directories(
        timeout=options.timeout,
        retries=options.retries,
    )
    stored_tags = _stored_tag_definitions(options.tags, catalog)
    definitions = merge_tag_definitions(definitions, stored_tags)
    stored_by_id = {tag["id"]: tag for tag in stored_tags}
    overrides = _read_json(options.overrides) if options.overrides.is_file() else {}
    definitions = _translate_definitions(
        definitions,
        stored_by_id,
        overrides,
        checkpoint,
        workers=options.workers,
        timeout=options.timeout,
        retries=options.retries,
        checkpoint_path=options.checkpoint,
    )

    completed = checkpoint.setdefault("products", {})
    pending = [
        record for record in targets if str(record.get("code")) not in completed
    ]
    failures = []
    if pending:
        with ThreadPoolExecutor(max_workers=options.workers) as executor:
            futures = {
                executor.submit(
                    _fetch_product,
                    record,
                    timeout=options.timeout,
                    retries=options.retries,
                ): record
                for record in pending
            }
            for future in as_completed(futures):
                record = futures[future]
                code = str(record.get("code") or "")
                try:
                    result = future.result()
                except Exception as error:
                    failures.append({"code": code, "error": str(error)})
                else:
                    completed[code] = result
                    _write_checkpoint(options.checkpoint, checkpoint)
                    print(f"tag-sync {len(completed)}/{len(targets)} {code}", flush=True)
    if failures:
        raise RuntimeError(
            "official tag sync failed without publishing: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )

    by_code = {str(record.get("code")): copy.deepcopy(record) for record in records}
    definition_by_id = {tag["id"]: tag for tag in definitions}
    for record in targets:
        code = str(record["code"])
        result = completed.get(code)
        if not isinstance(result, Mapping):
            raise RuntimeError(f"missing successful checkpoint for {code}")
        result_tags = result.get("tags")
        if not isinstance(result_tags, list):
            raise RuntimeError(f"invalid tag checkpoint for {code}")
        for tag in result_tags:
            if tag["id"] not in definition_by_id:
                # Detail pages can expose a newly-added tag before the directory.
                translated = _translate_definitions(
                    [tag],
                    stored_by_id,
                    overrides,
                    checkpoint,
                    workers=1,
                    timeout=options.timeout,
                    retries=options.retries,
                    checkpoint_path=options.checkpoint,
                )[0]
                definitions.append(translated)
                definition_by_id[translated["id"]] = translated
        by_code[code] = apply_product_detail(
            by_code[code],
            result["detail"],
            result_tags,
            stamp,
        )

    # The official catalog retains a very small number of withdrawn archive
    # entries with no live product detail route.  Resolve those explicitly as
    # an empty, provenance-labelled tag set instead of inventing classifications.
    for code, record in list(by_code.items()):
        if not _positive_int(record.get("productId")):
            by_code[code] = mark_unavailable_product_tags(record, stamp)

    enriched = [by_code[code] for code in sorted(by_code)]
    selected_links = {
        code: copy.deepcopy(record["links"])
        for code, record in by_code.items()
        if isinstance(record.get("links"), Mapping) and record["links"]
    }
    series_links = {
        str(series["code"]): copy.deepcopy(series["links"])
        for series in catalog.get("series", [])
        if isinstance(series, Mapping)
        and isinstance(series.get("links"), Mapping)
        and series["links"]
    }
    previous_refresh = catalog.get("refresh") if isinstance(catalog, Mapping) else {}
    refresh_context = {
        "mode": "incremental",
        "scanComplete": False,
        "requireTags": True,
        "inputs": copy.deepcopy(previous_refresh.get("inputs", {}))
        if isinstance(previous_refresh, Mapping)
        else {},
    }
    rebuilt, summary = build_catalog(
        enriched,
        selected_links,
        generated_at=stamp,
        previous_catalog=catalog,
        refresh_context=refresh_context,
        resources=catalog.get("resources") if isinstance(catalog, Mapping) else None,
        series_links=series_links,
        tags=definitions,
    )
    errors = validate_catalog(
        rebuilt,
        catalog,
        mode="incremental",
        refresh_context=refresh_context,
    )
    if errors:
        raise RuntimeError("tagged catalog validation failed:\n" + "\n".join(errors))
    result = {
        "targets": len(targets),
        "videos": len(enriched),
        "tags": len(rebuilt.get("tags", [])),
        "complete": sum(record.get("tagsStatus") == "complete" for record in enriched),
        "dryRun": bool(options.dry_run),
        "summary": summary["counts"],
    }
    if options.dry_run:
        return result

    raw_products = []
    for record in enriched:
        clean = copy.deepcopy(record)
        clean.pop("links", None)
        raw_products.append(clean)
    raw_tags = {
        "schemaVersion": 1,
        "updatedAt": stamp,
        "tags": normalize_tag_definitions(definitions),
    }
    _commit_transaction(
        [
            (options.products, _json_bytes(raw_products)),
            (options.tags, _json_bytes(raw_tags)),
            (options.catalog, serialize_catalog(rebuilt)),
        ],
        replacer=None,
        stale_remover=None,
    )
    if options.checkpoint.is_file():
        options.checkpoint.unlink()
    return result


def _fetch_tag_directories(*, timeout: float, retries: int) -> list:
    session = create_session(timeout=timeout, retries=retries, delay_seconds=0.5)
    definitions = []
    for group, url in TAG_DIRECTORY_URLS.items():
        response = _get_with_retry(session, url, timeout=timeout, retries=retries)
        definitions.extend(parse_tag_directory(response.text, group))
    return definitions


def _fetch_product(record: Mapping[str, object], *, timeout: float, retries: int) -> dict:
    session = getattr(_THREAD_STATE, "session", None)
    if session is None:
        session = create_session(timeout=timeout, retries=retries, delay_seconds=0.5)
        _THREAD_STATE.session = session
    product_id = int(record["productId"])
    url = PRODUCT_URL.format(product_id=product_id)
    response = _get_with_retry(session, url, timeout=timeout, retries=retries)
    detail = parse_product_page(response.text, product_id)
    if detail is None:
        raise RuntimeError(f"{record.get('code')} returned a non-product page")
    if detail.get("code") != record.get("code"):
        raise RuntimeError(
            f"{record.get('code')} detail code mismatch: {detail.get('code')}"
        )
    return {"detail": detail, "tags": parse_product_tags(response.text)}


def _get_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    retries: int,
):
    error = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=False)
            if response.status_code == 200:
                return response
            error = RuntimeError(
                f"HTTP {response.status_code} {response.headers.get('Location', '')}".strip()
            )
        except (requests.RequestException, OSError) as caught:
            error = caught
        if attempt + 1 < retries:
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"request failed after {retries} attempts: {url}: {error}")


def _translate_definitions(
    definitions: Sequence[Mapping[str, object]],
    stored_by_id: Mapping[int, Mapping[str, object]],
    overrides: Mapping[str, str],
    checkpoint: dict,
    *,
    workers: int,
    timeout: float,
    retries: int,
    checkpoint_path: Path,
) -> list:
    translations = checkpoint.setdefault("translations", {})
    prepared = []
    needs_translation = []
    for definition in definitions:
        item = copy.deepcopy(dict(definition))
        name_ja = str(item.get("nameJa") or "").strip()
        override = overrides.get(name_ja)
        stored = stored_by_id.get(item.get("id"), {})
        if isinstance(override, str) and override.strip():
            item["nameZh"] = override.strip()
            item["translationSource"] = "reviewed"
        elif (
            stored.get("nameJa") == name_ja
            and isinstance(stored.get("nameZh"), str)
            and stored["nameZh"].strip()
        ):
            item["nameZh"] = stored["nameZh"].strip()
            item["translationSource"] = str(
                stored.get("translationSource") or "machine"
            )
        elif str(item.get("id")) in translations:
            item["nameZh"] = str(translations[str(item["id"])]).strip()
            item["translationSource"] = "machine"
        else:
            needs_translation.append(item)
        prepared.append(item)

    if needs_translation:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _translate_name,
                    item["nameJa"],
                    timeout=timeout,
                    retries=retries,
                ): item
                for item in needs_translation
            }
            for future in as_completed(futures):
                item = futures[future]
                translated = future.result()
                item["nameZh"] = translated
                item["translationSource"] = "machine"
                translations[str(item["id"])] = translated
                _write_checkpoint(checkpoint_path, checkpoint)
    return normalize_tag_definitions(prepared, overrides)


def _translate_name(name: str, *, timeout: float, retries: int) -> str:
    error = None
    for attempt in range(retries):
        try:
            response = requests.get(
                TRANSLATE_URL,
                params={
                    "client": "gtx",
                    "sl": "ja",
                    "tl": "zh-CN",
                    "dt": "t",
                    "q": name,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            translated = "".join(
                str(part[0])
                for part in payload[0]
                if isinstance(part, list) and part and part[0]
            ).strip()
            if translated:
                return translated
            error = RuntimeError("translation response was empty")
        except (requests.RequestException, ValueError, TypeError, IndexError) as caught:
            error = caught
        if attempt + 1 < retries:
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"translation failed for {name!r}: {error}")


def _stored_tag_definitions(path: Path, catalog: Mapping[str, object]) -> list:
    if path.is_file():
        payload = _read_json(path)
        if isinstance(payload, Mapping) and isinstance(payload.get("tags"), list):
            return normalize_tag_definitions(payload["tags"])
    tags = catalog.get("tags") if isinstance(catalog, Mapping) else None
    return normalize_tag_definitions(tags if isinstance(tags, list) else [])


def _flatten_catalog(catalog: Mapping[str, object]) -> list:
    records = []
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if isinstance(video, Mapping):
                records.append(copy.deepcopy(dict(video)))
    return records


def _load_checkpoint(path: Path) -> dict:
    if not path.is_file():
        return {"schemaVersion": 1, "products": {}, "translations": {}}
    value = _read_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise RuntimeError("invalid tag sync checkpoint")
    if not isinstance(value.get("products"), dict) or not isinstance(
        value.get("translations"), dict
    ):
        raise RuntimeError("invalid tag sync checkpoint")
    return value


def _write_checkpoint(path: Path, value: Mapping[str, object]) -> None:
    _write_atomic(path, _json_bytes(value))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(value: object):
    if not isinstance(value, str):
        return None
    try:
        return _parse_timestamp(value)
    except ValueError:
        return None


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def main() -> int:
    try:
        result = run_sync()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
