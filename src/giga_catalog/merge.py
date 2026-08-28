"""Canonical product merging and deterministic public catalog generation."""

import copy
import json
from datetime import date
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from src.giga_catalog.codes import normalize_code
from src.giga_catalog.previews import preview_descriptor_from_cover
from src.giga_catalog.tags import build_public_tag_index


_PROVIDERS = ("gofile", "player4me", "streamtape", "subtitle", "vidara")
_MOJIBAKE_MARKERS = ("\ufffd", "丐", "乓", "涓", "涔")


def serialize_catalog(catalog: Mapping[str, object]) -> bytes:
    """Serialize one compact deterministic UTF-8 JSON document with one LF."""
    return (
        json.dumps(
            catalog,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def build_catalog(
    products: Sequence[Mapping[str, object]],
    links: Mapping[str, Mapping[str, object]],
    *,
    generated_at: str,
    previous_catalog: Optional[Mapping[str, object]] = None,
    refresh_context: Optional[Mapping[str, object]] = None,
    resources: Optional[Mapping[str, object]] = None,
    series_links: Optional[Mapping[str, Mapping[str, object]]] = None,
    tags: Optional[Sequence[Mapping[str, object]]] = None,
) -> Tuple[dict, dict]:
    """Build the deployable catalog and a richer private deterministic summary."""
    context = dict(refresh_context or {})
    mode = str(context.get("mode") or "incremental")
    previous_records = _flatten_catalog(previous_catalog)
    incoming_records, product_diagnostics, duplicate_count = _select_products(products)
    archive_retained_codes = _archive_retained_codes(
        incoming_records,
        previous_records,
        mode=mode,
        context=context,
    )
    current_records = _apply_retention(
        incoming_records,
        previous_records,
        mode=mode,
        context=context,
    )
    if mode != "links-only":
        current_records = _backfill_preview_descriptors(current_records)

    normalized_links, link_diagnostics = _normalize_links(links)
    _preserve_previous_video_subtitles(
        normalized_links,
        previous_records,
        link_diagnostics,
    )
    normalized_series_links, series_link_diagnostics = _normalize_series_links(
        series_links or {}
    )
    previous_series_links = _catalog_series_links(previous_catalog)
    normalized_series_links, preserved_series_diagnostics = _append_only_records(
        previous_series_links,
        normalized_series_links,
        scope="series",
    )
    previous_resources = _catalog_resources(previous_catalog)
    incoming_resources = (
        _clean_link_record(resources) if isinstance(resources, Mapping) else {}
    )
    selected_resources = incoming_resources or previous_resources
    resource_diagnostics: List[dict] = []
    current_with_links = {}
    for code in sorted(current_records):
        video = copy.deepcopy(current_records[code])
        video.pop("links", None)
        selected_links = normalized_links.get(code)
        if selected_links:
            video["links"] = copy.deepcopy(selected_links)
        current_with_links[code] = video

    previous_by_code = {
        code: copy.deepcopy(record) for code, record in sorted(previous_records.items())
    }
    diff = _diff_catalogs(previous_by_code, current_with_links)
    series = _build_series(current_with_links.values(), normalized_series_links)
    linked_videos = sum(
        1 for video in current_with_links.values() if video.get("links")
    )
    public_counts = {
        "added": len(diff["added"]),
        "updated": len(diff["updated"]),
        "retained": len(diff["retained"]),
        "deleted": len(diff["deleted"]),
        "linked": linked_videos,
        "linkAdded": len(diff["linkAdded"]),
        "linkUpdated": len(diff["linkUpdated"]),
        "linkRemoved": len(diff["linkRemoved"]),
        "linkConflicts": _nonnegative_int(context.get("linkConflicts"))
        + sum(
            1
            for item in (
                link_diagnostics
                + series_link_diagnostics
                + preserved_series_diagnostics
                + resource_diagnostics
            )
            if item.get("type") == "conflict"
        ),
    }
    public_refresh = {
        "mode": mode,
        "sourceComplete": bool(
            context.get("scanComplete", context.get("sourceComplete", False))
        ),
        "counts": public_counts,
    }
    inputs = context.get("inputs")
    if isinstance(inputs, Mapping):
        public_refresh["inputs"] = {
            str(key): copy.deepcopy(inputs[key]) for key in sorted(inputs)
        }

    catalog = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "totals": {
            "series": len(series),
            "videos": len(current_with_links),
            "linkedVideos": linked_videos,
        },
        "refresh": public_refresh,
        "series": series,
    }
    if selected_resources:
        catalog["resources"] = selected_resources
    selected_tags = tags
    if selected_tags is None and isinstance(previous_catalog, Mapping):
        previous_tags = previous_catalog.get("tags")
        if isinstance(previous_tags, list):
            selected_tags = previous_tags
    if selected_tags is not None:
        catalog["tags"] = build_public_tag_index(
            list(current_with_links.values()),
            selected_tags,
        )
    diagnostics = sorted(
        product_diagnostics
        + link_diagnostics
        + series_link_diagnostics
        + preserved_series_diagnostics
        + resource_diagnostics,
        key=lambda value: _fingerprint(value),
    )
    internal_summary = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "mode": mode,
        "sourceComplete": public_refresh["sourceComplete"],
        "counts": {
            "inputProducts": len(products),
            "acceptedProducts": len(incoming_records),
            "publishedProducts": len(current_with_links),
            "discardedDuplicates": duplicate_count,
            "diagnostics": len(diagnostics),
            "archiveRetained": len(archive_retained_codes),
            **public_counts,
        },
        "codes": {
            **{key: diff[key] for key in sorted(diff)},
            "archiveRetained": archive_retained_codes,
        },
        "diagnostics": diagnostics,
    }
    return catalog, internal_summary


def _select_products(
    products: Sequence[Mapping[str, object]]
) -> Tuple[Dict[str, dict], List[dict], int]:
    candidates: Dict[str, List[dict]] = {}
    diagnostics: List[dict] = []
    for source in products:
        canonical, diagnostic = _canonical_product(source)
        if canonical is None:
            diagnostics.append(diagnostic)
            continue
        candidates.setdefault(canonical["code"], []).append(canonical)

    selected: Dict[str, dict] = {}
    duplicate_count = 0
    for code in sorted(candidates):
        ranked = sorted(candidates[code], key=_candidate_order)
        selected[code] = ranked[-1]
        duplicate_count += len(ranked) - 1
        for discarded in ranked[:-1]:
            diagnostics.append(
                {
                    "type": "discarded_duplicate",
                    "code": code,
                    "fingerprint": _fingerprint(discarded),
                }
            )
    return selected, diagnostics, duplicate_count


def _canonical_product(source: Mapping[str, object]) -> Tuple[Optional[dict], dict]:
    if not isinstance(source, Mapping):
        return None, {"type": "invalid_product", "reason": "not_mapping"}
    code = normalize_code(source.get("code"))
    if code is None:
        return None, {
            "type": "invalid_product",
            "reason": "invalid_code",
            "fingerprint": _fingerprint(source),
        }
    series, number_text = code.rsplit("-", 1)
    number = int(number_text)
    supplied_series = source.get("series")
    if supplied_series is not None and supplied_series != series:
        return None, {
            "type": "invalid_product",
            "code": code,
            "reason": "series_mismatch",
        }
    supplied_number = source.get("number")
    if supplied_number is not None:
        try:
            if int(supplied_number) != number:
                raise ValueError
        except (TypeError, ValueError):
            return None, {
                "type": "invalid_product",
                "code": code,
                "reason": "number_mismatch",
            }

    release_date = source.get("releaseDate", source.get("date"))
    title = source.get("title")
    actors = source.get("actors", [])
    cover = source.get("cover")
    canonical = {
        "code": code,
        "number": number,
        "title": title,
        "actors": copy.deepcopy(actors),
        "releaseDate": release_date,
        "cover": cover,
    }
    product_id = _positive_int(source.get("productId"))
    if product_id is not None:
        canonical["productId"] = product_id
    if "previewBase" in source or "previewCount" in source:
        preview_base = source.get("previewBase")
        if isinstance(preview_base, str) and preview_base.strip():
            canonical["previewBase"] = preview_base
        preview_count = _positive_int(source.get("previewCount"))
        if preview_count is not None:
            canonical["previewCount"] = preview_count
    if "tagIds" in source:
        tag_ids = source.get("tagIds")
        if isinstance(tag_ids, list):
            canonical["tagIds"] = sorted(
                {
                    value
                    for value in tag_ids
                    if isinstance(value, int)
                    and not isinstance(value, bool)
                    and value > 0
                }
            )
    tags_status = source.get("tagsStatus")
    if isinstance(tags_status, str) and tags_status.strip():
        canonical["tagsStatus"] = tags_status.strip()
    tags_updated_at = source.get("tagsUpdatedAt")
    if isinstance(tags_updated_at, str) and tags_updated_at.strip():
        canonical["tagsUpdatedAt"] = tags_updated_at.strip()
    tags_source = source.get("tagsSource")
    if tags_source in {"official", "official-unavailable"}:
        canonical["tagsSource"] = tags_source
    existing_links = source.get("links")
    if isinstance(existing_links, Mapping):
        cleaned = _clean_link_record(existing_links)
        if cleaned:
            canonical["links"] = cleaned
    return canonical, {}


def _backfill_preview_descriptors(
    records: Mapping[str, Mapping[str, object]]
) -> Dict[str, dict]:
    """Add bounded legacy preview candidates after the old snapshot is preserved."""
    enriched: Dict[str, dict] = {}
    for code in sorted(records):
        record = copy.deepcopy(dict(records[code]))
        if (
            "previewBase" not in record
            and "previewCount" not in record
            and _positive_int(record.get("productId")) is not None
        ):
            record.update(preview_descriptor_from_cover(record.get("cover")))
        enriched[code] = record
    return enriched


def _candidate_order(candidate: Mapping[str, object]) -> tuple:
    actors = candidate.get("actors")
    return (
        _positive_int(candidate.get("productId")) is not None,
        _valid_date(candidate.get("releaseDate")),
        _valid_http_url(candidate.get("cover")),
        isinstance(actors, list)
        and any(isinstance(actor, str) and actor.strip() for actor in actors),
        isinstance(candidate.get("title"), str)
        and bool(candidate["title"].strip())
        and not _has_mojibake(candidate["title"]),
        _fingerprint(candidate),
    )


def _apply_retention(
    incoming: Mapping[str, dict],
    previous: Mapping[str, dict],
    *,
    mode: str,
    context: Mapping[str, object],
) -> Dict[str, dict]:
    if not previous:
        return {code: copy.deepcopy(incoming[code]) for code in sorted(incoming)}
    incoming = {
        code: _preserve_preview_precision(previous.get(code), incoming[code])
        for code in sorted(incoming)
    }
    if mode == "links-only":
        return {code: copy.deepcopy(previous[code]) for code in sorted(previous)}
    if mode == "incremental" or not bool(context.get("scanComplete")):
        merged = {
            code: copy.deepcopy(previous[code]) for code in sorted(previous)
        }
        # Incoming candidates have already undergone permutation-independent
        # duplicate selection.  For a known code they represent the current
        # source observation and therefore update, rather than compete with,
        # the retained snapshot.
        merged.update(
            {code: copy.deepcopy(incoming[code]) for code in sorted(incoming)}
        )
        return {code: merged[code] for code in sorted(merged)}
    if mode != "audit":
        return {code: copy.deepcopy(incoming[code]) for code in sorted(incoming)}

    bounded = _audit_is_bounded(context)
    if not bounded:
        selected = {
            code: copy.deepcopy(previous[code])
            for code in _archive_retained_codes(
                incoming,
                previous,
                mode=mode,
                context=context,
            )
        }
        selected.update(
            {code: copy.deepcopy(incoming[code]) for code in sorted(incoming)}
        )
        return {code: selected[code] for code in sorted(selected)}

    selected = {}
    for code, record in sorted(previous.items()):
        if not _in_scope(record, context):
            selected[code] = copy.deepcopy(record)
    for code, record in sorted(incoming.items()):
        if _in_scope(record, context):
            selected[code] = copy.deepcopy(record)
    return selected


def _archive_retained_codes(
    incoming: Mapping[str, dict],
    previous: Mapping[str, dict],
    *,
    mode: str,
    context: Mapping[str, object],
) -> List[str]:
    if mode != "audit" or not bool(context.get("scanComplete")):
        return []
    if _audit_is_bounded(context):
        return []
    return sorted(
        code
        for code, record in previous.items()
        if code not in incoming and _positive_int(record.get("productId")) is None
    )


def _audit_is_bounded(context: Mapping[str, object]) -> bool:
    explicit = context.get("auditBounded")
    if isinstance(explicit, bool):
        return explicit
    return any(
        context.get(name) is not None
        for name in ("startId", "endId", "minReleaseDate", "maxReleaseDate")
    )


def _preserve_preview_precision(
    previous: Optional[Mapping[str, object]],
    incoming: Mapping[str, object],
) -> dict:
    selected = copy.deepcopy(dict(incoming))
    if not isinstance(previous, Mapping):
        return selected
    if (
        "previewBase" not in selected
        and "previewCount" not in selected
        and _positive_int(selected.get("productId")) is not None
    ):
        selected.update(preview_descriptor_from_cover(selected.get("cover")))
    previous_base = previous.get("previewBase")
    incoming_base = selected.get("previewBase")
    previous_count = _positive_int(previous.get("previewCount"))
    incoming_count = _positive_int(selected.get("previewCount"))
    if (
        previous_base == incoming_base
        and previous_count is not None
        and incoming_count is not None
        and previous_count > incoming_count
    ):
        selected["previewCount"] = previous_count
    return selected


def _in_scope(record: Mapping[str, object], context: Mapping[str, object]) -> bool:
    product_id = _positive_int(record.get("productId"))
    start_id = _positive_int(context.get("startId"))
    end_id = _positive_int(context.get("endId"))
    if start_id is not None and (product_id is None or product_id < start_id):
        return False
    if end_id is not None and (product_id is None or product_id > end_id):
        return False
    release_date = record.get("releaseDate")
    min_date = context.get("minReleaseDate")
    max_date = context.get("maxReleaseDate")
    if isinstance(min_date, str) and (
        not isinstance(release_date, str) or release_date < min_date
    ):
        return False
    if isinstance(max_date, str) and (
        not isinstance(release_date, str) or release_date > max_date
    ):
        return False
    return True


def _normalize_links(
    links: Mapping[str, Mapping[str, object]]
) -> Tuple[Dict[str, dict], List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    diagnostics: List[dict] = []
    for raw_code, raw_record in links.items():
        code = normalize_code(raw_code)
        if code is None:
            diagnostics.append(
                {"type": "invalid_link_code", "code": str(raw_code)}
            )
            continue
        if not isinstance(raw_record, Mapping):
            diagnostics.append({"type": "invalid_link_record", "code": code})
            continue
        cleaned = _clean_link_record(raw_record)
        if cleaned:
            grouped.setdefault(code, []).append(cleaned)

    normalized: Dict[str, dict] = {}
    for code in sorted(grouped):
        result = {}
        for candidate in sorted(grouped[code], key=_fingerprint):
            result, conflicts = _merge_link_records(result, candidate, code)
            diagnostics.extend(conflicts)
        if result:
            normalized[code] = result
    return normalized, diagnostics


def _preserve_previous_video_subtitles(
    links: Dict[str, dict],
    previous: Mapping[str, Mapping[str, object]],
    diagnostics: List[dict],
) -> None:
    for code in sorted(previous):
        previous_links = previous[code].get("links")
        if not isinstance(previous_links, Mapping):
            continue
        previous_subtitle = previous_links.get("subtitle")
        if not isinstance(previous_subtitle, str) or not previous_subtitle.strip():
            continue
        previous_subtitle = previous_subtitle.strip()
        current = links.setdefault(code, {})
        incoming = current.get("subtitle")
        if incoming is None:
            current["subtitle"] = previous_subtitle
        elif incoming != previous_subtitle:
            diagnostics.append(
                {
                    "type": "conflict",
                    "code": code,
                    "provider": "subtitle",
                    "values": sorted([previous_subtitle, str(incoming)]),
                    "selected": previous_subtitle,
                }
            )
            current["subtitle"] = previous_subtitle


def _normalize_series_links(
    links: Mapping[str, Mapping[str, object]]
) -> Tuple[Dict[str, dict], List[dict]]:
    normalized: Dict[str, dict] = {}
    diagnostics: List[dict] = []
    for raw_series in sorted(links, key=str):
        canonical = normalize_code(f"{raw_series}-1")
        if canonical is None:
            diagnostics.append(
                {"type": "invalid_series_link_code", "code": str(raw_series)}
            )
            continue
        series = canonical.rsplit("-", 1)[0]
        record = links[raw_series]
        if not isinstance(record, Mapping):
            diagnostics.append(
                {"type": "invalid_series_link_record", "code": series}
            )
            continue
        cleaned = _clean_link_record(record)
        if not cleaned:
            continue
        existing = normalized.get(series, {})
        merged, conflicts = _merge_link_records(existing, cleaned, series)
        normalized[series] = merged
        diagnostics.extend(conflicts)
    return normalized, diagnostics


def _catalog_series_links(
    catalog: Optional[Mapping[str, object]],
) -> Dict[str, dict]:
    if not isinstance(catalog, Mapping):
        return {}
    result = {}
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping) or not isinstance(series.get("code"), str):
            continue
        links = series.get("links")
        if isinstance(links, Mapping):
            cleaned = _clean_link_record(links)
            if cleaned:
                result[series["code"]] = cleaned
    return result


def _catalog_resources(catalog: Optional[Mapping[str, object]]) -> Dict[str, dict]:
    if not isinstance(catalog, Mapping):
        return {}
    resources = catalog.get("resources")
    if not isinstance(resources, Mapping):
        return {}
    return _clean_link_record(resources)


def _append_only_records(
    previous: Mapping[str, object],
    incoming: Mapping[str, object],
    *,
    scope: str,
) -> Tuple[Dict[str, dict], List[dict]]:
    selected = copy.deepcopy(dict(previous))
    diagnostics: List[dict] = []
    for key in sorted(incoming):
        value = incoming[key]
        if not isinstance(value, Mapping):
            continue
        existing = selected.get(key)
        if existing is None:
            selected[key] = copy.deepcopy(dict(value))
            continue
        merged, conflicts = _merge_append_only_record(
            dict(existing) if isinstance(existing, Mapping) else {},
            dict(value),
            f"{scope}:{key}",
        )
        selected[key] = merged
        diagnostics.extend(conflicts)
    return {key: selected[key] for key in sorted(selected)}, diagnostics


def _merge_append_only_record(
    previous: Mapping[str, object],
    incoming: Mapping[str, object],
    code: str,
) -> Tuple[dict, List[dict]]:
    result = copy.deepcopy(dict(previous))
    conflicts = []
    for key in sorted(incoming):
        value = incoming[key]
        existing = result.get(key)
        if isinstance(value, Mapping):
            nested, nested_conflicts = _merge_append_only_record(
                existing if isinstance(existing, Mapping) else {},
                value,
                code,
            )
            result[key] = nested
            conflicts.extend(nested_conflicts)
        elif existing is None:
            result[key] = copy.deepcopy(value)
        elif existing != value:
            conflicts.append(
                {
                    "type": "conflict",
                    "code": code,
                    "provider": key,
                    "values": sorted([str(existing), str(value)]),
                    "selected": str(existing),
                }
            )
    return result, conflicts


def _clean_link_record(record: Mapping[str, object]) -> dict:
    cleaned = {}
    for key in sorted(record):
        value = record[key]
        if isinstance(value, Mapping):
            nested = _clean_link_record(value)
            if nested:
                cleaned[str(key)] = nested
        elif isinstance(value, str) and value.strip():
            cleaned[str(key)] = value.strip()
    return cleaned


def _merge_link_records(base: dict, incoming: dict, code: str) -> Tuple[dict, List[dict]]:
    result = copy.deepcopy(base)
    conflicts = []
    for key in sorted(incoming):
        value = incoming[key]
        existing = result.get(key)
        if isinstance(value, dict):
            nested, nested_conflicts = _merge_link_records(
                existing if isinstance(existing, dict) else {},
                value,
                code,
            )
            result[key] = nested
            conflicts.extend(nested_conflicts)
        elif existing is None:
            result[key] = value
        elif existing != value:
            winner = min(str(existing), str(value))
            conflicts.append(
                {
                    "type": "conflict",
                    "code": code,
                    "provider": key,
                    "values": sorted([str(existing), str(value)]),
                    "selected": winner,
                }
            )
            result[key] = winner
    return result, conflicts


def _flatten_catalog(catalog: Optional[Mapping[str, object]]) -> Dict[str, dict]:
    if not isinstance(catalog, Mapping):
        return {}
    records = []
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if isinstance(video, Mapping):
                records.append(video)
    selected, _, _ = _select_products(records)
    return selected


def _diff_catalogs(previous: Mapping[str, dict], current: Mapping[str, dict]) -> dict:
    previous_codes = set(previous)
    current_codes = set(current)
    added = sorted(current_codes - previous_codes)
    deleted = sorted(previous_codes - current_codes)
    updated = []
    retained = []
    link_added = []
    link_updated = []
    link_removed = []
    for code in sorted(previous_codes & current_codes):
        old_metadata = _without_links(previous[code])
        new_metadata = _without_links(current[code])
        (updated if old_metadata != new_metadata else retained).append(code)
    for code in sorted(previous_codes | current_codes):
        old_links = previous.get(code, {}).get("links")
        new_links = current.get(code, {}).get("links")
        if not old_links and new_links:
            link_added.append(code)
        elif old_links and not new_links:
            link_removed.append(code)
        elif old_links and new_links and old_links != new_links:
            link_updated.append(code)
    return {
        "added": added,
        "deleted": deleted,
        "linkAdded": link_added,
        "linkRemoved": link_removed,
        "linkUpdated": link_updated,
        "retained": retained,
        "updated": updated,
    }


def _without_links(record: Mapping[str, object]) -> dict:
    return {key: copy.deepcopy(value) for key, value in record.items() if key != "links"}


def _build_series(
    videos: Iterable[Mapping[str, object]],
    series_links: Optional[Mapping[str, Mapping[str, object]]] = None,
) -> List[dict]:
    groups: Dict[str, List[dict]] = {}
    for source in videos:
        video = copy.deepcopy(dict(source))
        code = video["code"]
        series_code = code.rsplit("-", 1)[0]
        groups.setdefault(series_code, []).append(video)
    series = []
    for series_code in sorted(groups):
        ordered = sorted(
            groups[series_code],
            key=lambda video: (video["number"], video["code"]),
        )
        dates = [str(video.get("releaseDate") or "") for video in ordered]
        item = {
            "code": series_code,
            "count": len(ordered),
            "firstReleaseDate": min(dates),
            "latestReleaseDate": max(dates),
            "videos": ordered,
        }
        selected_links = (series_links or {}).get(series_code)
        if selected_links:
            item["links"] = copy.deepcopy(dict(selected_links))
        series.append(item)
    series.sort(key=lambda item: item["code"])
    series.sort(key=lambda item: item["latestReleaseDate"], reverse=True)
    return series


def _positive_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _has_mojibake(value: str) -> bool:
    return "\ufffd" in value or any(value.count(marker) >= 2 for marker in _MOJIBAKE_MARKERS[1:])


def _fingerprint(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return repr(value)
