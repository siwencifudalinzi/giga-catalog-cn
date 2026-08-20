"""End-to-end deterministic GIGA catalog refresh command."""

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.migrate_legacy import migrate_legacy  # noqa: E402
from src.giga_catalog.codes import normalize_code  # noqa: E402
from src.giga_catalog.featured_covers import cache_featured_covers  # noqa: E402
from src.giga_catalog.merge import build_catalog, serialize_catalog  # noqa: E402
from src.giga_catalog.scraper import BASE_URL, discover_products  # noqa: E402
from src.giga_catalog.sheet import download_sheet, parse_sheet_csv  # noqa: E402
from src.giga_catalog.subtitles import (  # noqa: E402
    SubtitleFormatError,
    download_subtitle_source,
    parse_subtitle_child_csv,
    parse_subtitle_directory_html,
    parse_subtitle_directory_xlsx,
    validate_subtitle_manifest,
)
from src.giga_catalog.validation import (  # noqa: E402
    DEFAULT_MIN_RELEASE_DATE,
    validate_catalog,
    validate_stored_catalog,
)


DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/"
    "export?format=csv&gid=0"
)
DEFAULT_SUBTITLE_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/"
    "htmlview/sheet?pli=1&headers=true&gid=0"
)
DEFAULT_BASE_URL = BASE_URL
DEFAULT_LEGACY_DIR = Path(r"D:\giga-catalog")
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "public"
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / "data"
TAIL_FALLBACK_LIMIT = 25


class RefreshError(RuntimeError):
    """A refresh that must not publish."""


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("incremental", "audit", "links-only"),
        default="incremental",
    )
    parser.add_argument("--legacy-dir", type=Path, default=DEFAULT_LEGACY_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start-id", type=int)
    parser.add_argument("--end-id", type=int)
    parser.add_argument(
        "--min-release-date",
        default=DEFAULT_MIN_RELEASE_DATE,
    )
    parser.add_argument("--sheet-url", default=DEFAULT_SHEET_URL)
    parser.add_argument("--subtitle-url", default=DEFAULT_SUBTITLE_URL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--strict-links", action="store_true")
    return parser


def run_refresh(
    argv: Optional[Sequence[str]] = None,
    *,
    sheet_downloader: Optional[Callable] = None,
    subtitle_downloader: Optional[Callable] = None,
    discoverer: Optional[Callable] = None,
    validator: Optional[Callable] = None,
    replacer: Optional[Callable] = None,
    stale_remover: Optional[Callable] = None,
    clock: Optional[Callable] = None,
    migrator: Optional[Callable] = None,
    featured_cover_refresher: Optional[Callable] = None,
) -> dict:
    """Execute a refresh and return both public and private in-memory summaries."""
    options = create_parser().parse_args(list(argv) if argv is not None else None)
    _validate_options(options)
    sheet_downloader = sheet_downloader or download_sheet
    subtitle_downloader = subtitle_downloader or download_subtitle_source
    discoverer = discoverer or discover_products
    validator = validator or validate_catalog
    migrator = migrator or migrate_legacy
    featured_cover_refresher = featured_cover_refresher or cache_featured_covers
    generated_at = _generated_at(clock)

    output_root = options.output_root.resolve()
    data_root = options.data_root.resolve()
    catalog_path = output_root / "data" / "catalog.json"
    state_path = data_root / "state" / "scrape-state.json"
    previous_catalog = _load_previous_catalog(catalog_path)
    previous_last_audit_at = _load_last_audit_at(state_path)
    previous_subtitle_state = _load_subtitle_state(state_path)

    seed_products: List[dict] = []
    legacy_links: Dict[str, dict] = {}
    legacy_facts = {
        "seeded": False,
        "products": 0,
        "linkKeys": 0,
        "dataSha256": None,
        "linksSha256": None,
    }
    if previous_catalog is None:
        data_path = options.legacy_dir / "data.json"
        links_path = options.legacy_dir / "links.json"
        if not data_path.is_file() or not links_path.is_file():
            raise RefreshError(
                "previous catalog is missing and --legacy-dir lacks data.json/links.json"
            )
        seed_products, legacy_links = migrator(data_path, links_path)
        legacy_facts = {
            "seeded": True,
            "products": len(seed_products),
            "linkKeys": len(legacy_links),
            "dataSha256": _hash_file(data_path),
            "linksSha256": _hash_file(links_path),
        }

    sheet_text = sheet_downloader(
        options.sheet_url,
        timeout=options.timeout,
        retries=options.retries,
        delay_seconds=options.delay,
    )
    if not isinstance(sheet_text, str):
        raise RefreshError("sheet downloader did not return text")
    sheet_links, sheet_diagnostics = parse_sheet_csv(sheet_text)
    prior_links = (
        legacy_links
        if previous_catalog is None
        else _extract_catalog_links(previous_catalog)
    )
    selected_links = _overlay_links(prior_links, sheet_links)

    existing_products = (
        _flatten_catalog(previous_catalog)
        if previous_catalog is not None
        else copy.deepcopy(seed_products)
    )
    discovery_summary = {
        "mode": options.mode,
        "errors": 0,
        "stopReason": "links_only",
        "newProducts": 0,
    }
    if options.mode == "links-only":
        candidate_products = copy.deepcopy(seed_products)
        # No official GIGA source was contacted in links-only mode.
        scan_complete = False
    else:
        discovered, discovery_summary = _discover_with_tail_fallback(
            discoverer,
            existing_products,
            options,
            bool(existing_products),
        )
        discovered = _filter_products(discovered, options)
        if previous_catalog is None and options.mode == "incremental":
            candidate_products = copy.deepcopy(seed_products) + discovered
        else:
            candidate_products = discovered
        scan_complete = discovery_summary.get("authoritativeComplete") is True

    catalog_codes = _product_codes(existing_products + candidate_products)
    catalog_series = {code.rsplit("-", 1)[0] for code in catalog_codes}
    subtitle_payload = subtitle_downloader(
        options.subtitle_url,
        timeout=options.timeout,
        retries=options.retries,
        delay_seconds=options.delay,
    )
    if isinstance(subtitle_payload, bytes):
        subtitle_directory = parse_subtitle_directory_xlsx(
            subtitle_payload,
            source_url=options.subtitle_url,
            catalog_series=catalog_series,
        )
    elif isinstance(subtitle_payload, str):
        subtitle_directory = parse_subtitle_directory_html(
            subtitle_payload,
            source_url=options.subtitle_url,
            catalog_series=catalog_series,
        )
    else:
        raise RefreshError("subtitle downloader did not return text or XLSX bytes")
    subtitle_video_links = {}
    child_evidence = []
    for child in subtitle_directory.child_sources:
        child_text = subtitle_downloader(
            child.csv_url,
            timeout=options.timeout,
            retries=options.retries,
            delay_seconds=options.delay,
        )
        if not isinstance(child_text, str):
            raise RefreshError("subtitle child downloader did not return text")
        child_links = parse_subtitle_child_csv(
            child_text,
            series=child.series,
            catalog_codes=catalog_codes,
        )
        child_evidence.append(
            {
                "series": child.series,
                "sourceUrl": child.source_url,
                "csvUrl": child.csv_url,
                "links": child_links,
            }
        )
        for code in sorted(child_links):
            subtitle_video_links[code] = child_links[code]

    subtitle_raw = validate_subtitle_manifest(
        _subtitle_evidence(
            options.subtitle_url,
            subtitle_directory,
            child_evidence,
            subtitle_video_links,
        ),
        require_sha256=False,
    )
    subtitle_sha256 = _hash_bytes(_json_bytes(subtitle_raw))
    subtitle_state = validate_subtitle_manifest(
        {
            **copy.deepcopy(subtitle_raw),
            "sha256": subtitle_sha256,
        }
    )
    _validate_subtitle_continuity(
        previous_subtitle_state,
        subtitle_state,
    )
    subtitle_state_changed = (
        previous_subtitle_state is None
        or previous_subtitle_state.get("sha256") != subtitle_sha256
    )
    selected_links = _overlay_links(
        selected_links,
        {
            code: {"subtitle": url}
            for code, url in sorted(subtitle_video_links.items())
        },
    )
    selected_series_links = {
        series: {"subtitle": url}
        for series, url in sorted(subtitle_directory.series_links.items())
    }
    selected_resources = {
        "subtitleDirectory": {
            "label": "SRT ENGSUB DOWNLOAD",
            "url": subtitle_directory.portal_url,
        }
    }

    inputs = {
        "productsSha256": _hash_logical_products(candidate_products),
        "sheetSha256": _hash_bytes(sheet_text.encode("utf-8")),
        "subtitleSha256": subtitle_sha256,
    }
    refresh_context = {
        "mode": options.mode,
        "scanComplete": scan_complete,
        "startId": options.start_id,
        "endId": options.end_id,
        "minReleaseDate": options.min_release_date,
        "auditBounded": bool(
            options.start_id is not None
            or options.end_id is not None
            or options.min_release_date != DEFAULT_MIN_RELEASE_DATE
        ),
        "strictLinks": options.strict_links,
        "inputs": inputs,
        "linkConflicts": len(sheet_diagnostics),
    }
    catalog, internal_summary = build_catalog(
        candidate_products,
        selected_links,
        generated_at=generated_at,
        previous_catalog=previous_catalog,
        refresh_context=refresh_context,
        resources=selected_resources,
        series_links=selected_series_links,
    )
    internal_summary["sources"] = {
        "legacy": legacy_facts,
        "sheet": {
            "url": options.sheet_url,
            "linkKeys": len(sheet_links),
            "diagnostics": sorted(
                copy.deepcopy(sheet_diagnostics),
                key=_json_fingerprint,
            ),
            "sha256": inputs["sheetSha256"],
        },
        "official": _json_safe_mapping(discovery_summary),
        "subtitles": {
            "url": options.subtitle_url,
            "sha256": subtitle_sha256,
            "legendColor": subtitle_directory.legend_color,
            "portalCount": 1,
            "pinkSources": subtitle_directory.pink_source_count,
            "seriesLinks": len(subtitle_directory.series_links),
            "childSheets": len(subtitle_directory.child_sources),
            "videoLinks": len(subtitle_video_links),
            "unresolvedCount": len(subtitle_directory.unresolved_sources),
            "unresolved": [
                {
                    "series": source.series,
                    "url": source.url,
                    "reason": source.reason,
                }
                for source in subtitle_directory.unresolved_sources
            ],
            "diagnostics": [],
        },
    }
    candidate_bytes = serialize_catalog(catalog)
    internal_summary["candidateSha256"] = _hash_bytes(candidate_bytes)

    errors = validator(
        catalog,
        previous_catalog,
        mode=options.mode,
        refresh_context=refresh_context,
    )
    if errors:
        raise RefreshError(
            "catalog validation failed:\n" + "\n".join(sorted(str(error) for error in errors))
        )

    content_changed = (
        previous_catalog is None
        or _effective_catalog(previous_catalog) != _effective_catalog(catalog)
    )
    authoritative_audit = options.mode == "audit" and scan_complete
    publication_required = (
        authoritative_audit
        if options.mode == "audit"
        else content_changed or subtitle_state_changed
    )
    last_audit_at = generated_at if authoritative_audit else previous_last_audit_at
    result = {
        "dryRun": bool(options.dry_run),
        "changed": publication_required,
        "humanSummary": _human_summary(
            catalog,
            internal_summary,
            options.dry_run,
            publication_required,
        ),
        "publicRefresh": copy.deepcopy(catalog["refresh"]),
        "internal": internal_summary,
    }
    if options.dry_run:
        return result

    if publication_required:
        _publish(
            catalog_path,
            candidate_bytes,
            data_root,
            sheet_text,
            _catalog_metadata_products(catalog),
            internal_summary,
            discovery_summary,
            inputs,
            generated_at,
            last_audit_at,
            subtitle_raw,
            subtitle_state,
            replacer,
            stale_remover,
        )
    try:
        result["featuredCovers"] = featured_cover_refresher(
            catalog_path,
            output_root / "media" / "featured-covers",
            output_root / "data" / "featured-covers.json",
        )
    except Exception as error:
        # Covers are an optional LCP optimization.  A source outage must never
        # turn an already-published catalog into a failed synchronization.
        result["featuredCovers"] = {"published": False, "error": str(error)}
    return result


def _discover_with_tail_fallback(
    discoverer: Callable,
    existing_products: List[dict],
    options,
    has_baseline: bool,
) -> Tuple[List[dict], dict]:
    """Use the directory first, then one bounded tail recovery when it is unusable."""
    common = {
        "delay_seconds": options.delay,
        "base_url": options.base_url,
        "timeout": options.timeout,
        "retries": options.retries,
    }
    directory_records: List[dict] = []
    try:
        result = discoverer(
            existing_products,
            mode=options.mode,
            include_known=options.mode == "audit",
            **common,
        )
    except Exception as error:
        raise RefreshError(
            "official directory discovery failed: "
            f"{type(error).__name__}: {error}"
        ) from error
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not isinstance(result[0], list)
        or not isinstance(result[1], Mapping)
    ):
        raise RefreshError("official directory discovery failed: invalid result")
    directory_records = result[0]
    try:
        directory_summary = _json_safe_mapping(result[1])
    except Exception as error:
        raise RefreshError(
            "official directory discovery failed: invalid result: "
            f"{type(error).__name__}: {error}"
        ) from error

    if directory_summary.get("cardIntegrityComplete") is False:
        reason = directory_summary.get("error") or "unresolved_directory_cards"
        raise RefreshError(
            f"official directory card integrity failed: {reason}"
        )

    if not _directory_requires_tail(
        directory_records,
        directory_summary,
        options.mode,
        has_baseline,
    ):
        directory_summary["authoritativeComplete"] = bool(
            options.mode == "audit"
            and directory_summary.get("stopReason") == "empty"
        )
        return directory_records, directory_summary

    try:
        tail_result = discoverer(
            existing_products,
            mode="tail",
            page_limit=TAIL_FALLBACK_LIMIT,
            include_known=False,
            **common,
        )
    except Exception as error:
        raise RefreshError(
            f"tail fallback failed: {type(error).__name__}: {error}"
        ) from error
    if (
        not isinstance(tail_result, tuple)
        or len(tail_result) != 2
        or not isinstance(tail_result[0], list)
        or not isinstance(tail_result[1], Mapping)
    ):
        raise RefreshError("tail fallback failed: invalid result")
    tail_records = tail_result[0]
    tail_summary = _json_safe_mapping(tail_result[1])
    tail_errors = _summary_count(tail_summary, "errors")
    if tail_errors is None or tail_errors > 0 or tail_summary.get("stopReason") == "error":
        reason = tail_summary.get("error") or tail_summary.get("stopReason") or "unknown error"
        raise RefreshError(f"tail fallback failed: {reason}")

    records = sorted(
        (
            copy.deepcopy(record)
            for record in directory_records + tail_records
        ),
        key=_json_fingerprint,
    )
    summary = {
        "mode": options.mode,
        "fallbackUsed": True,
        "directory": directory_summary,
        "fallback": tail_summary,
        "pagesFetched": (_summary_count(directory_summary, "pagesFetched") or 0)
        + (_summary_count(tail_summary, "pagesFetched") or 0),
        "parsedProducts": (_summary_count(directory_summary, "parsedProducts") or 0)
        + (_summary_count(tail_summary, "parsedProducts") or 0),
        "newProducts": (_summary_count(directory_summary, "newProducts") or 0)
        + (_summary_count(tail_summary, "newProducts") or 0),
        "knownProducts": (_summary_count(directory_summary, "knownProducts") or 0)
        + (_summary_count(tail_summary, "knownProducts") or 0),
        "cursor": tail_summary.get("cursor"),
        "retries": (_summary_count(directory_summary, "retries") or 0)
        + (_summary_count(tail_summary, "retries") or 0),
        "errors": (_summary_count(directory_summary, "errors") or 0),
        "stopReason": "tail_fallback",
        "authoritativeComplete": False,
    }
    return records, summary


def _directory_requires_tail(
    records: Sequence[object],
    summary: Mapping[str, object],
    expected_mode: str,
    has_baseline: bool,
) -> bool:
    if not _valid_directory_summary(records, summary, expected_mode):
        return True
    if _summary_count(summary, "errors") > 0:
        return True
    pages_fetched = _summary_count(summary, "pagesFetched")
    parsed_products = _summary_count(summary, "parsedProducts")
    return bool(
        has_baseline
        and pages_fetched >= 1
        and parsed_products == 0
    )


def _valid_directory_summary(
    records: Sequence[object],
    summary: Mapping[str, object],
    expected_mode: str,
) -> bool:
    if summary.get("mode") != expected_mode:
        return False
    count_keys = (
        "pagesFetched",
        "parsedProducts",
        "newProducts",
        "knownProducts",
        "retries",
        "errors",
    )
    counts = {key: _summary_count(summary, key) for key in count_keys}
    if any(value is None for value in counts.values()):
        return False
    cursor = summary.get("cursor")
    if (
        not isinstance(cursor, int)
        or isinstance(cursor, bool)
        or cursor <= 0
    ):
        return False
    stop_reason = summary.get("stopReason")
    allowed_stops = (
        {"empty", "all_known", "page_limit", "error"}
        if expected_mode == "incremental"
        else {"empty", "page_limit", "error"}
    )
    if stop_reason not in allowed_stops:
        return False
    errors = counts["errors"]
    if (errors > 0) != (stop_reason == "error"):
        return False
    if errors > 0:
        return True
    if counts["parsedProducts"] != (
        counts["newProducts"] + counts["knownProducts"]
    ):
        return False
    expected_records = (
        counts["parsedProducts"]
        if expected_mode == "audit"
        else counts["newProducts"]
    )
    if len(records) != expected_records:
        return False
    if stop_reason == "empty":
        return (
            counts["pagesFetched"] >= 1
            and cursor == counts["pagesFetched"]
        )
    if stop_reason == "all_known":
        return counts["pagesFetched"] >= 2
    return True


def _summary_count(summary: Mapping[str, object], key: str) -> Optional[int]:
    value = summary.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _publish(
    catalog_path: Path,
    candidate_bytes: bytes,
    data_root: Path,
    sheet_text: str,
    candidate_products: Sequence[Mapping[str, object]],
    internal_summary: Mapping[str, object],
    discovery_summary: Mapping[str, object],
    inputs: Mapping[str, object],
    generated_at: str,
    last_audit_at: Optional[str],
    subtitle_raw: Mapping[str, object],
    subtitle_state: Mapping[str, object],
    replacer: Optional[Callable],
    stale_remover: Optional[Callable],
) -> None:
    """Commit all public/private artifacts as one recoverable filesystem transaction."""
    stale_public_summary = catalog_path.with_name("update-summary.json")
    state = {
        "schemaVersion": 1,
        "lastSuccessfulGeneration": "sha256:" + _hash_bytes(candidate_bytes),
        "lastSuccessfulAt": generated_at,
        "official": {
            "cursor": discovery_summary.get("cursor"),
            "complete": discovery_summary.get("authoritativeComplete") is True,
            "lastAuditAt": last_audit_at,
        },
        "inputs": copy.deepcopy(dict(inputs)),
        "subtitle": copy.deepcopy(dict(subtitle_state)),
    }
    private_outputs = {
        data_root / "raw" / "products.json": _json_bytes(
            sorted(
                (copy.deepcopy(dict(item)) for item in candidate_products),
                key=_json_fingerprint,
            )
        ),
        data_root / "raw" / "sheet.csv": sheet_text.encode("utf-8"),
        data_root / "raw" / "subtitles.json": _json_bytes(subtitle_raw),
        data_root / "state" / "scrape-state.json": _json_bytes(state),
        data_root / "update-summary.json": _json_bytes(internal_summary),
    }
    operations: List[Tuple[Path, Optional[bytes]]] = [
        (catalog_path, candidate_bytes),
        (stale_public_summary, None),
        *private_outputs.items(),
    ]
    _commit_transaction(operations, replacer, stale_remover)


def _commit_transaction(
    operations: Sequence[Tuple[Path, Optional[bytes]]],
    replacer: Optional[Callable],
    stale_remover: Optional[Callable],
) -> None:
    """Stage complete replacement/rollback material before touching a live target."""
    created_directories: List[Path] = []
    snapshots: Dict[Path, bool] = {}
    temporary_paths = {
        target: target.with_name(f".{target.name}.refresh.tmp")
        for target, content in operations
        if content is not None
    }
    backup_paths = {
        target: target.with_name(f".{target.name}.refresh.bak")
        for target, _ in operations
    }
    rollback_paths = {
        target: target.with_name(f".{target.name}.refresh.rollback")
        for target, _ in operations
    }
    artifacts = [
        *temporary_paths.values(),
        *backup_paths.values(),
        *rollback_paths.values(),
    ]

    try:
        for target, _ in operations:
            _ensure_directory(target.parent, created_directories)
        for artifact in artifacts:
            _remove_temporary(artifact)

        # Snapshot and fsync every existing original before staging replacements.
        for target, _ in operations:
            existed = target.exists()
            if existed:
                if not target.is_file():
                    raise OSError(f"transaction target is not a file: {target}")
                _write_synced(backup_paths[target], target.read_bytes())
                snapshots[target] = True
            else:
                snapshots[target] = False

        # Every replacement is fully written and fsynced before the first mutation.
        for target, content in operations:
            if content is not None:
                _write_synced(temporary_paths[target], content)

        for target, content in operations:
            if content is None:
                if target.exists():
                    if stale_remover is None:
                        target.unlink()
                    else:
                        stale_remover(target)
                continue
            temporary = temporary_paths[target]
            if replacer is None:
                os.replace(str(temporary), str(target))
            else:
                replacer(temporary, target)
    except BaseException as error:
        rollback_errors = _restore_transaction(
            operations,
            snapshots,
            backup_paths,
            rollback_paths,
        )
        try:
            _cleanup_transaction_artifacts(artifacts)
        except OSError:
            pass
        _remove_created_empty_directories(created_directories)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise RefreshError(
                f"publish failed and rollback was incomplete: {error}; {details}"
            ) from error
        raise

    try:
        _cleanup_transaction_artifacts(artifacts)
    except OSError:
        pass


def _restore_transaction(
    operations: Sequence[Tuple[Path, Optional[bytes]]],
    snapshots: Mapping[Path, bool],
    backup_paths: Mapping[Path, Path],
    rollback_paths: Mapping[Path, Path],
) -> List[str]:
    """Restore with native filesystem calls, bypassing an injected failing replacer."""
    errors = []
    for target, _ in operations:
        if target not in snapshots:
            continue
        try:
            if snapshots[target]:
                backup = backup_paths[target]
                rollback = rollback_paths[target]
                _write_synced(rollback, backup.read_bytes())
                os.replace(str(rollback), str(target))
            elif target.exists():
                target.unlink()
        except BaseException as error:
            errors.append(f"{target}: {error}")
    return errors


def _ensure_directory(path: Path, created: List[Path]) -> None:
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    if not cursor.is_dir():
        raise OSError(f"transaction parent is not a directory: {cursor}")
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _cleanup_transaction_artifacts(paths: Sequence[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def _remove_created_empty_directories(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _load_previous_catalog(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    if not path.is_file():
        raise RefreshError(f"previous catalog path is not a file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RefreshError(f"previous catalog is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise RefreshError("previous catalog must be a JSON object")
    prior_errors = validate_stored_catalog(value)
    if prior_errors:
        raise RefreshError(
            "previous catalog is invalid:\n" + "\n".join(prior_errors)
        )
    return value


def _load_last_audit_at(path: Path) -> Optional[str]:
    """Return only a parseable timezone-aware timestamp from prior private state."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    official = value.get("official")
    if not isinstance(official, Mapping):
        return None
    timestamp = official.get("lastAuditAt")
    if not isinstance(timestamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if parsed.tzinfo is not None else None


def _load_subtitle_state(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    if not path.is_file():
        raise RefreshError(f"subtitle state path is not a file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RefreshError(f"scrape state is unreadable: {error}") from error
    if not isinstance(value, dict):
        raise RefreshError("scrape state must be a JSON object")
    subtitle = value.get("subtitle")
    if subtitle is None:
        return None
    try:
        return validate_subtitle_manifest(subtitle)
    except SubtitleFormatError as error:
        raise RefreshError(f"subtitle state is malformed: {error}") from error


def _validate_subtitle_continuity(
    previous: Optional[Mapping[str, object]],
    current: Mapping[str, object],
) -> None:
    if previous is None:
        return
    if previous.get("legendColor") != current.get("legendColor"):
        raise RefreshError("subtitle legend changed from the previous verified state")
    old_resolved = previous.get("resolvedSources")
    new_resolved = current.get("resolvedSources")
    if not isinstance(old_resolved, list) or not isinstance(new_resolved, list):
        raise RefreshError("subtitle state resolvedSources is malformed")
    new_by_key = {
        _subtitle_source_key(source): source
        for source in new_resolved
        if isinstance(source, Mapping)
    }
    for source in old_resolved:
        if not isinstance(source, Mapping):
            raise RefreshError("subtitle state resolvedSources is malformed")
        key = _subtitle_source_key(source)
        if key not in new_by_key or dict(source) != dict(new_by_key[key]):
            raise RefreshError(
                "previous resolved subtitle source disappeared or changed: "
                + ":".join(key)
            )


def _subtitle_source_key(source: Mapping[str, object]) -> Tuple[str, ...]:
    scope = source.get("scope")
    series = source.get("series")
    code = source.get("code")
    if scope == "series" and isinstance(series, str):
        return ("series", series)
    if scope == "video" and isinstance(series, str) and isinstance(code, str):
        return ("video", series, code)
    raise RefreshError("subtitle state contains an invalid resolved source")


def _flatten_catalog(catalog: Mapping[str, object]) -> List[dict]:
    products = []
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if isinstance(video, Mapping):
                products.append(copy.deepcopy(dict(video)))
    return products


def _product_codes(products: Sequence[Mapping[str, object]]) -> set:
    codes = set()
    for product in products:
        if not isinstance(product, Mapping):
            continue
        code = product.get("code")
        if isinstance(code, str):
            from src.giga_catalog.codes import normalize_code

            normalized = normalize_code(code)
            if normalized is not None:
                codes.add(normalized)
    return codes


def _subtitle_evidence(
    source_url: str,
    directory,
    child_evidence: Sequence[Mapping[str, object]],
    video_links: Mapping[str, str],
) -> dict:
    resolved = [
        {
            "scope": "series",
            "series": series,
            "url": url,
        }
        for series, url in sorted(directory.series_links.items())
    ]
    resolved.extend(
        {
            "scope": "video",
            "series": code.rsplit("-", 1)[0],
            "code": code,
            "url": url,
        }
        for code, url in sorted(video_links.items())
    )
    return {
        "schemaVersion": 1,
        "sourceUrl": source_url,
        "legendColor": directory.legend_color,
        "portal": {
            "label": "SRT ENGSUB DOWNLOAD",
            "url": directory.portal_url,
        },
        "resolvedSources": resolved,
        "unresolvedSources": [
            {
                "series": source.series,
                "url": source.url,
                "reason": source.reason,
            }
            for source in directory.unresolved_sources
        ],
        "childSources": [
            copy.deepcopy(dict(item))
            for item in sorted(child_evidence, key=_json_fingerprint)
        ],
    }


def _extract_catalog_links(catalog: Mapping[str, object]) -> Dict[str, dict]:
    links = {}
    for product in _flatten_catalog(catalog):
        code = product.get("code")
        record = product.get("links")
        if isinstance(code, str) and isinstance(record, Mapping) and record:
            links[code] = copy.deepcopy(dict(record))
    return links


def _catalog_metadata_products(catalog: Mapping[str, object]) -> List[dict]:
    products = _flatten_catalog(catalog)
    for product in products:
        product.pop("links", None)
    return products


def _filter_products(products: Sequence[Mapping[str, object]], options) -> List[dict]:
    result = []
    for source in products:
        if not isinstance(source, Mapping):
            result.append(source)
            continue
        product_id = source.get("productId")
        if options.start_id is not None and (
            not isinstance(product_id, int) or product_id < options.start_id
        ):
            continue
        if options.end_id is not None and (
            not isinstance(product_id, int) or product_id > options.end_id
        ):
            continue
        release_date = source.get("releaseDate", source.get("date"))
        if not isinstance(release_date, str) or release_date < options.min_release_date:
            continue
        code = source.get("code")
        if normalize_code(code) is None:
            raise RefreshError(f"official product code is not publishable: {code}")
        result.append(copy.deepcopy(dict(source)))
    return result


def _overlay_links(base: Mapping[str, dict], overlay: Mapping[str, dict]) -> Dict[str, dict]:
    result = copy.deepcopy(dict(base))
    for code in sorted(overlay):
        result[code] = _overlay_link_record(result.get(code, {}), overlay[code])
    return result


def _overlay_link_record(base: Mapping[str, object], overlay: Mapping[str, object]) -> dict:
    result = copy.deepcopy(dict(base))
    for key in sorted(overlay):
        value = overlay[key]
        if isinstance(value, Mapping):
            result[key] = _overlay_link_record(
                result.get(key, {}) if isinstance(result.get(key), Mapping) else {},
                value,
            )
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_options(options) -> None:
    output_root = options.output_root.resolve()
    data_root = options.data_root.resolve()
    if _paths_overlap(output_root, data_root):
        raise RefreshError("public and private paths must be disjoint")

    public_targets = [
        (output_root / "data" / "catalog.json").resolve(),
        (output_root / "data" / "update-summary.json").resolve(),
    ]
    private_targets = [
        (data_root / "raw" / "products.json").resolve(),
        (data_root / "raw" / "sheet.csv").resolve(),
        (data_root / "raw" / "subtitles.json").resolve(),
        (data_root / "state" / "scrape-state.json").resolve(),
        (data_root / "update-summary.json").resolve(),
    ]
    if (
        any(not _path_is_within(path, output_root) for path in public_targets)
        or any(not _path_is_within(path, data_root) for path in private_targets)
        or any(
            _paths_overlap(public_path, private_path)
            for public_path in public_targets
            for private_path in private_targets
        )
    ):
        raise RefreshError("public and private paths must be disjoint")
    options.output_root = output_root
    options.data_root = data_root
    if options.start_id is not None and options.start_id <= 0:
        raise RefreshError("--start-id must be positive")
    if options.end_id is not None and options.end_id <= 0:
        raise RefreshError("--end-id must be positive")
    if (
        options.start_id is not None
        and options.end_id is not None
        and options.start_id > options.end_id
    ):
        raise RefreshError("--start-id must not exceed --end-id")
    try:
        if date.fromisoformat(options.min_release_date).isoformat() != options.min_release_date:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise RefreshError("--min-release-date must be a real ISO date") from error
    if options.timeout <= 0:
        raise RefreshError("--timeout must be positive")
    if options.retries <= 0:
        raise RefreshError("--retries must be positive")
    if options.delay < 0:
        raise RefreshError("--delay must be non-negative")
    for flag, value in (
        ("--sheet-url", options.sheet_url),
        ("--subtitle-url", options.subtitle_url),
        ("--base-url", options.base_url),
    ):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RefreshError(f"{flag} must be an HTTP(S) URL")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _generated_at(clock: Optional[Callable]) -> str:
    value = clock() if clock is not None else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc).replace(microsecond=0)
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    raise RefreshError("clock must return a datetime or RFC3339 string")


def _write_synced(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _write_private_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        _write_synced(temporary, content)
        os.replace(str(temporary), str(path))
    except BaseException:
        _remove_temporary(temporary)
        raise


def _remove_temporary(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_logical_products(products: Sequence[Mapping[str, object]]) -> str:
    ordered = sorted(
        (copy.deepcopy(dict(item)) for item in products if isinstance(item, Mapping)),
        key=_json_fingerprint,
    )
    return _hash_bytes(_json_bytes(ordered))


def _json_fingerprint(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_safe_mapping(value: Mapping[str, object]) -> dict:
    return json.loads(_json_fingerprint(dict(value)))


def _effective_catalog(catalog: Mapping[str, object]) -> str:
    """Fingerprint deployable data while ignoring refresh bookkeeping."""
    return _json_fingerprint(
        {
            "schemaVersion": catalog.get("schemaVersion"),
            "totals": catalog.get("totals"),
            "resources": catalog.get("resources"),
            "tags": catalog.get("tags"),
            "series": catalog.get("series"),
        }
    )


def _human_summary(
    catalog: Mapping[str, object],
    internal_summary: Mapping[str, object],
    dry_run: bool,
    changed: bool,
) -> str:
    totals = catalog["totals"]
    counts = catalog["refresh"]["counts"]
    if not changed:
        prefix = "DRY RUN UNCHANGED" if dry_run else "UNCHANGED"
    else:
        prefix = "DRY RUN CHANGED" if dry_run else "PUBLISHED"
    return (
        f"{prefix}: {totals['videos']} videos / {totals['series']} series / "
        f"{totals['linkedVideos']} linked; +{counts['added']} "
        f"~{counts['updated']} ={counts['retained']} -{counts['deleted']}; "
        f"diagnostics={internal_summary['counts']['diagnostics']}"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        result = run_refresh(argv)
    except (RefreshError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(result["humanSummary"])
    print(
        json.dumps(
            result["internal"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
