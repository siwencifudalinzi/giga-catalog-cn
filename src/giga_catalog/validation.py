"""Release gates for deployable GIGA catalog documents."""

import json
import re
from datetime import date, datetime
from typing import Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from src.giga_catalog.previews import (
    is_giga_preview_base,
    preview_base_from_cover,
)


DEFAULT_MIN_RELEASE_DATE = "2007-12-07"
DEFAULT_MAX_REGRESSION_FRACTION = 0.15
DEFAULT_MAX_GLOBAL_DELETIONS = 100
DEFAULT_MAX_SERIES_DELETIONS = 25

_SERIES_RE = re.compile(r"[A-Z0-9]+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PROVIDERS = {"streamtape", "player4me", "vidara", "gofile", "subtitle"}
_UNCENSORED_PROVIDERS = {"streamtape", "player4me", "vidara", "gofile"}
_REFRESH_MODES = {"incremental", "audit", "links-only"}
_REFRESH_COUNT_KEYS = {
    "added",
    "updated",
    "retained",
    "deleted",
    "linked",
    "linkAdded",
    "linkUpdated",
    "linkRemoved",
    "linkConflicts",
}
_MOJIBAKE_MARKERS = ("\ufffd",)
_REPEATED_MOJIBAKE_CHARS = ("丐", "乓", "涓", "涔")


def validate_catalog(
    catalog: Mapping[str, object],
    previous: Optional[Mapping[str, object]] = None,
    mode: str = "incremental",
    refresh_context: Optional[Mapping[str, object]] = None,
) -> List[str]:
    """Return deterministic human-readable errors; an empty list means publishable."""
    context = dict(refresh_context or {})
    errors: List[str] = []
    if mode not in _REFRESH_MODES:
        errors.append(f"unknown refresh mode: {mode}")
    if not isinstance(catalog, Mapping):
        return ["catalog must be an object"]

    if catalog.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    if not _valid_generated_at(catalog.get("generatedAt")):
        errors.append("generatedAt must be a UTC RFC3339 timestamp ending in Z")

    series_items = catalog.get("series")
    if not isinstance(series_items, list):
        return sorted(errors + ["series must be an array"])
    if not series_items:
        errors.append("catalog is empty")

    videos: Dict[str, dict] = {}
    product_id_codes: Dict[int, List[str]] = {}
    series_counts: Dict[str, int] = {}
    linked_videos = 0
    min_release_date = context.get("catalogMinReleaseDate", DEFAULT_MIN_RELEASE_DATE)
    strict_links = bool(context.get("strictLinks", False))
    link_conflicts = _positive_int(context.get("linkConflicts"), 0)
    if strict_links and link_conflicts > 0:
        errors.append(f"strict link conflicts: {link_conflicts}")
    seen_series = set()

    for series_index, series in enumerate(series_items):
        path = f"series[{series_index}]"
        if not isinstance(series, Mapping):
            errors.append(f"{path} must be an object")
            continue
        series_code = series.get("code")
        if not isinstance(series_code, str) or _SERIES_RE.fullmatch(series_code) is None:
            errors.append(f"{path}.code is invalid")
            series_code = str(series_code)
        if series_code in seen_series:
            errors.append(f"duplicate series code: {series_code}")
        seen_series.add(series_code)
        series_videos = series.get("videos")
        if not isinstance(series_videos, list):
            errors.append(f"{path}.videos must be an array")
            continue
        if not series_videos:
            errors.append(f"{path}.videos must not be empty")
        series_counts[series_code] = len(series_videos)
        if series.get("count") != len(series_videos):
            errors.append(f"{path}.count mismatch")
        if "links" in series:
            errors.extend(_validate_series_links(series.get("links"), path))

        dates = []
        prior_sort_key = None
        for video_index, video in enumerate(series_videos):
            video_path = f"{path}.videos[{video_index}]"
            if not isinstance(video, Mapping):
                errors.append(f"{video_path} must be an object")
                continue
            code = video.get("code")
            number = video.get("number")
            expected_code = (
                f"{series_code}-{number}"
                if isinstance(number, int) and not isinstance(number, bool) and number >= 0
                else None
            )
            if expected_code is None or code != expected_code:
                errors.append(f"{video_path} code/series/number mismatch")
            if not isinstance(code, str):
                code = str(code)
            if code in videos:
                errors.append(f"duplicate code: {code}")
            else:
                videos[code] = dict(video)
            if "productId" in video:
                product_id = video.get("productId")
                if (
                    not isinstance(product_id, int)
                    or isinstance(product_id, bool)
                    or product_id <= 0
                ):
                    errors.append(f"{video_path}.productId must be a positive integer")
                else:
                    product_id_codes.setdefault(product_id, []).append(code)
            sort_key = (number if isinstance(number, int) else -1, code)
            if prior_sort_key is not None and sort_key < prior_sort_key:
                errors.append(f"{path}.videos are not sorted")
            prior_sort_key = sort_key

            title = video.get("title")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{video_path}.title must be non-empty text")
            elif _has_mojibake(title):
                errors.append(f"{video_path}.title contains mojibake")

            actors = video.get("actors")
            if not isinstance(actors, list):
                errors.append(f"{video_path}.actors must be an array")
            else:
                for actor_index, actor in enumerate(actors):
                    if not isinstance(actor, str) or not actor.strip():
                        errors.append(
                            f"{video_path}.actors[{actor_index}] must be non-empty text"
                        )
                    elif _has_mojibake(actor):
                        errors.append(
                            f"{video_path}.actors[{actor_index}] contains mojibake"
                        )

            release_date = video.get("releaseDate")
            if not _valid_date(release_date):
                errors.append(f"{video_path}.releaseDate is not a real ISO date")
            else:
                dates.append(release_date)
                if isinstance(min_release_date, str) and release_date < min_release_date:
                    errors.append(
                        f"{video_path}.releaseDate is before minimum release date "
                        f"{min_release_date}"
                    )

            cover = video.get("cover")
            if cover is not None and not _valid_http_url(cover):
                errors.append(f"{video_path}.cover is not a valid HTTP(S) URL")

            links = video.get("links")
            if links is not None:
                link_errors = _validate_links(links, video_path)
                errors.extend(link_errors)
                if isinstance(links, Mapping) and _has_link_leaf(links):
                    linked_videos += 1
            if strict_links and not (
                isinstance(links, Mapping) and _has_link_leaf(links)
            ):
                errors.append(f"{video_path} fails strict link coverage")

            if "previewUrls" in video:
                errors.append(f"{video_path}.previewUrls must not be published")
            preview_base = video.get("previewBase")
            preview_count = video.get("previewCount")
            if preview_base is not None and not is_giga_preview_base(preview_base):
                errors.append(
                    f"{video_path}.previewBase is not an official GIGA sample URL"
                )
            if (
                preview_base is not None
                and preview_base != preview_base_from_cover(cover)
            ):
                errors.append(
                    f"{video_path}.previewBase does not match cover"
                )
            if preview_count is not None and (
                not isinstance(preview_count, int)
                or isinstance(preview_count, bool)
                or preview_count <= 0
                or preview_count > 99
            ):
                errors.append(
                    f"{video_path}.previewCount must be an integer from 1 to 99"
                )
            if (preview_base is None) != (preview_count is None):
                errors.append(
                    f"{video_path}.previewBase and previewCount must be provided together"
                )

        if dates:
            if series.get("firstReleaseDate") != min(dates):
                errors.append(f"{path}.firstReleaseDate mismatch")
            if series.get("latestReleaseDate") != max(dates):
                errors.append(f"{path}.latestReleaseDate mismatch")

    for product_id in sorted(product_id_codes):
        codes = sorted(set(product_id_codes[product_id]))
        if len(codes) > 1:
            errors.append(f"duplicate productId {product_id}: {', '.join(codes)}")

    sortable_series = [
        series
        for series in series_items
        if isinstance(series, Mapping)
        and isinstance(series.get("code"), str)
        and isinstance(series.get("latestReleaseDate"), str)
    ]
    expected_series = sorted(sortable_series, key=lambda item: item["code"])
    expected_series.sort(
        key=lambda item: item["latestReleaseDate"],
        reverse=True,
    )
    if sortable_series != expected_series:
        errors.append("series are not sorted by latestReleaseDate DESC, code ASC")

    if "resources" in catalog:
        errors.extend(_validate_resources(catalog.get("resources")))

    errors.extend(
        _validate_tags(
            catalog.get("tags"),
            videos,
            required=bool(context.get("requireTags", False)),
        )
    )

    totals = catalog.get("totals")
    if not isinstance(totals, Mapping):
        errors.append("totals must be an object")
    else:
        expected_totals = {
            "series": len(series_items),
            "videos": sum(series_counts.values()),
            "linkedVideos": linked_videos,
        }
        for key, expected in expected_totals.items():
            if totals.get(key) != expected:
                errors.append(f"totals.{key} mismatch: expected {expected}")

    previous_videos = _video_map(previous)
    errors.extend(
        _validate_refresh(
            catalog.get("refresh"),
            expected_mode=mode,
            current_videos=videos,
            previous_videos=previous_videos,
            expected_linked=linked_videos,
            context=context,
        )
    )
    errors.extend(_validate_mode(videos, previous_videos, mode, context))
    return sorted(set(errors))


def _validate_tags(value: object, videos: Mapping[str, dict], required: bool) -> List[str]:
    errors: List[str] = []
    if value is None:
        if required:
            errors.append("tags must be an array when tag coverage is required")
        return errors
    if not isinstance(value, list):
        return ["tags must be an array"]
    definitions = {}
    previous_id = 0
    for index, tag in enumerate(value):
        path = f"tags[{index}]"
        if not isinstance(tag, Mapping):
            errors.append(f"{path} must be an object")
            continue
        tag_id = tag.get("id")
        if not isinstance(tag_id, int) or isinstance(tag_id, bool) or tag_id <= 0:
            errors.append(f"{path}.id must be a positive integer")
            continue
        if tag_id in definitions:
            errors.append(f"duplicate tag id {tag_id}")
        if tag_id <= previous_id:
            errors.append("tags must be sorted by id")
        previous_id = tag_id
        definitions[tag_id] = tag
        if tag.get("group") not in {"genre", "character"}:
            errors.append(f"{path}.group is invalid")
        for name in ("nameJa", "nameZh"):
            if not isinstance(tag.get(name), str) or not tag[name].strip():
                errors.append(f"{path}.{name} must be non-empty text")
        count = tag.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(f"{path}.count must be a non-negative integer")

    expected_counts = {tag_id: 0 for tag_id in definitions}
    for code, video in videos.items():
        tag_ids = video.get("tagIds")
        if required and video.get("tagsStatus") != "complete":
            errors.append(f"{code}.tagsStatus must be complete")
        if required and not _valid_generated_at(video.get("tagsUpdatedAt")):
            errors.append(f"{code}.tagsUpdatedAt must be a UTC RFC3339 timestamp")
        if required and video.get("tagsSource") not in {
            "official",
            "official-unavailable",
        }:
            errors.append(f"{code}.tagsSource must identify official provenance")
        if tag_ids is None:
            if required:
                errors.append(f"{code}.tagIds must be an array")
            continue
        if not isinstance(tag_ids, list):
            errors.append(f"{code}.tagIds must be an array")
            continue
        if tag_ids != sorted(set(tag_ids)):
            errors.append(f"{code}.tagIds must be unique and sorted")
        for tag_id in set(tag_ids):
            if tag_id not in definitions:
                errors.append(f"{code} references unknown tag id {tag_id}")
            else:
                expected_counts[tag_id] += 1
    for tag_id, expected in expected_counts.items():
        actual = definitions[tag_id].get("count")
        if actual != expected:
            errors.append(
                f"tag count mismatch for {tag_id}: expected {expected}, got {actual}"
            )
    return errors


def validate_stored_catalog(catalog: Mapping[str, object]) -> List[str]:
    """Validate a standalone baseline whose prior generation is unavailable."""
    refresh = catalog.get("refresh") if isinstance(catalog, Mapping) else None
    stored_mode = (
        refresh.get("mode")
        if isinstance(refresh, Mapping)
        and refresh.get("mode") in _REFRESH_MODES
        else "incremental"
    )
    return validate_catalog(
        catalog,
        mode=stored_mode,
        refresh_context={"_historicalRefresh": True},
    )


def _validate_refresh(
    value: object,
    *,
    expected_mode: str,
    current_videos: Mapping[str, dict],
    previous_videos: Mapping[str, dict],
    expected_linked: int,
    context: Mapping[str, object],
) -> List[str]:
    if not isinstance(value, Mapping):
        return ["refresh must be an object"]

    errors: List[str] = []
    if value.get("mode") not in _REFRESH_MODES:
        errors.append("refresh.mode is invalid")
    elif value.get("mode") != expected_mode:
        errors.append(f"refresh.mode mismatch: expected {expected_mode}")
    if not isinstance(value.get("sourceComplete"), bool):
        errors.append("refresh.sourceComplete must be a boolean")
    else:
        expected_complete = None
        if "scanComplete" in context:
            expected_complete = bool(context.get("scanComplete"))
        elif "sourceComplete" in context:
            expected_complete = bool(context.get("sourceComplete"))
        if (
            expected_complete is not None
            and value.get("sourceComplete") != expected_complete
        ):
            expected_text = "true" if expected_complete else "false"
            errors.append(
                f"refresh.sourceComplete mismatch: expected {expected_text}"
            )

    counts = value.get("counts")
    if not isinstance(counts, Mapping):
        errors.append("refresh.counts must be an object")
    else:
        actual_keys = set(counts)
        missing = sorted(_REFRESH_COUNT_KEYS - actual_keys)
        extra = sorted(actual_keys - _REFRESH_COUNT_KEYS, key=str)
        if missing:
            errors.append(f"refresh.counts missing keys: {', '.join(missing)}")
        if extra:
            errors.append(
                "refresh.counts has unknown keys: "
                + ", ".join(str(key) for key in extra)
            )
        for key in sorted(_REFRESH_COUNT_KEYS & actual_keys):
            count = counts.get(key)
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                errors.append(f"refresh.counts.{key} must be a non-negative integer")
        current_keys = ("added", "updated", "retained")
        if all(
            isinstance(counts.get(key), int)
            and not isinstance(counts.get(key), bool)
            and counts.get(key) >= 0
            for key in current_keys
        ):
            current_count = sum(counts[key] for key in current_keys)
            if current_count != len(current_videos):
                errors.append(
                    "refresh.counts added+updated+retained mismatch: "
                    f"expected {len(current_videos)}"
                )
        linked = counts.get("linked")
        if (
            isinstance(linked, int)
            and not isinstance(linked, bool)
            and linked >= 0
            and linked != expected_linked
        ):
            errors.append(
                f"refresh.counts.linked mismatch: expected {expected_linked}"
            )

        expected_counts: Dict[str, int] = {}
        if not bool(context.get("_historicalRefresh")):
            expected_counts = _refresh_diff_counts(
                previous_videos,
                current_videos,
            )
        link_conflicts = context.get("linkConflicts")
        if (
            isinstance(link_conflicts, int)
            and not isinstance(link_conflicts, bool)
            and link_conflicts >= 0
        ):
            expected_counts["linkConflicts"] = link_conflicts
        for key in sorted(expected_counts):
            count = counts.get(key)
            if (
                isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
                and count != expected_counts[key]
            ):
                errors.append(
                    f"refresh.counts.{key} mismatch: "
                    f"expected {expected_counts[key]}"
                )

    if "inputs" in value:
        inputs = value.get("inputs")
        if not isinstance(inputs, Mapping):
            errors.append("refresh.inputs must be an object")
        else:
            for key in sorted(inputs, key=str):
                digest = inputs[key]
                if (
                    not isinstance(key, str)
                    or not key
                    or not isinstance(digest, str)
                    or _SHA256_RE.fullmatch(digest) is None
                ):
                    errors.append(
                        f"refresh.inputs.{key} must be a lowercase SHA-256 digest"
                    )
    return errors


def _refresh_diff_counts(
    previous: Mapping[str, dict],
    current: Mapping[str, dict],
) -> Dict[str, int]:
    previous_codes = set(previous)
    current_codes = set(current)
    counts = {
        "added": len(current_codes - previous_codes),
        "updated": 0,
        "retained": 0,
        "deleted": len(previous_codes - current_codes),
        "linkAdded": 0,
        "linkUpdated": 0,
        "linkRemoved": 0,
    }
    for code in sorted(previous_codes & current_codes):
        key = (
            "updated"
            if _metadata(previous[code]) != _metadata(current[code])
            else "retained"
        )
        counts[key] += 1
    for code in sorted(previous_codes | current_codes):
        old_links = previous.get(code, {}).get("links")
        new_links = current.get(code, {}).get("links")
        if not old_links and new_links:
            counts["linkAdded"] += 1
        elif old_links and not new_links:
            counts["linkRemoved"] += 1
        elif old_links and new_links and old_links != new_links:
            counts["linkUpdated"] += 1
    return counts


def _validate_links(value: object, video_path: str) -> List[str]:
    errors = []
    if not isinstance(value, Mapping):
        return [f"{video_path}.links must be an object"]
    if not value:
        errors.append(f"{video_path}.links must not be empty")
    for provider, url in value.items():
        if provider == "uncensored":
            if not isinstance(url, Mapping):
                errors.append(f"{video_path}.links.uncensored must be an object")
                continue
            if not url:
                errors.append(f"{video_path}.links.uncensored must not be empty")
            for nested_provider, nested_url in url.items():
                if nested_provider not in _UNCENSORED_PROVIDERS:
                    errors.append(
                        f"{video_path}.links.uncensored has unknown provider "
                        f"{nested_provider}"
                    )
                errors.extend(
                    _validate_link_url(
                        nested_url,
                        f"{video_path}.links.uncensored.{nested_provider}",
                    )
                )
            continue
        if provider not in _PROVIDERS:
            errors.append(f"{video_path}.links has unknown provider {provider}")
        errors.extend(_validate_link_url(url, f"{video_path}.links.{provider}"))
    return errors


def _validate_link_url(value: object, path: str) -> List[str]:
    if value == "":
        return [f"{path} is an empty link URL"]
    if not _valid_http_url(value):
        return [f"{path} is not a valid HTTP(S) URL"]
    return []


def _validate_series_links(value: object, series_path: str) -> List[str]:
    path = f"{series_path}.links"
    if not isinstance(value, Mapping):
        return [f"{path} must be an object"]
    errors = []
    if not value:
        errors.append(f"{path} must not be empty")
    for provider, url in value.items():
        if provider != "subtitle":
            errors.append(f"{path} has unknown provider {provider}")
        errors.extend(_validate_link_url(url, f"{path}.{provider}"))
        if provider == "subtitle" and _valid_http_url(url) and not _valid_drive_url(url):
            errors.append(f"{path}.subtitle is not a direct Google Drive URL")
    return errors


def _validate_resources(value: object) -> List[str]:
    if not isinstance(value, Mapping):
        return ["resources must be an object"]
    errors = []
    if not value:
        errors.append("resources must not be empty")
    for name in value:
        if name != "subtitleDirectory":
            errors.append(f"resources has unknown entry {name}")
    directory = value.get("subtitleDirectory")
    if directory is None:
        return errors
    if not isinstance(directory, Mapping):
        return errors + ["resources.subtitleDirectory must be an object"]
    expected_keys = {"label", "url"}
    actual_keys = set(directory)
    if actual_keys != expected_keys:
        errors.append("resources.subtitleDirectory must contain only label and url")
    if directory.get("label") != "SRT ENGSUB DOWNLOAD":
        errors.append("resources.subtitleDirectory.label is invalid")
    errors.extend(
        _validate_link_url(
            directory.get("url"),
            "resources.subtitleDirectory.url",
        )
    )
    return errors


def _validate_mode(
    current: Mapping[str, dict],
    previous: Mapping[str, dict],
    mode: str,
    context: Mapping[str, object],
) -> List[str]:
    if not previous:
        return []
    errors = []
    previous_codes = set(previous)
    current_codes = set(current)
    deleted = sorted(previous_codes - current_codes)

    previous_ids = _product_id_map(previous)
    current_ids = _product_id_map(current)
    for product_id in sorted(set(previous_ids) & set(current_ids)):
        if previous_ids[product_id] != current_ids[product_id]:
            errors.append(
                f"productId {product_id} remapped from {previous_ids[product_id]} "
                f"to {current_ids[product_id]}"
            )

    if mode == "incremental":
        errors.extend(f"incremental removed {code}" for code in deleted)
        return errors
    if mode == "links-only":
        for code in sorted(previous_codes ^ current_codes):
            errors.append(f"links-only product set changed: {code}")
        for code in sorted(previous_codes & current_codes):
            if _metadata(previous[code]) != _metadata(current[code]):
                errors.append(f"links-only metadata changed: {code}")
        return errors
    if mode != "audit" or not deleted:
        return errors

    if not bool(context.get("scanComplete")):
        errors.append("audit scan is incomplete; deletion is forbidden")
        return errors

    for code in deleted:
        if not _in_audit_scope(previous[code], context):
            errors.append(f"audit removed {code} outside audit bounds")

    max_fraction = _fraction(
        context.get("maxRegressionFraction"), DEFAULT_MAX_REGRESSION_FRACTION
    )
    global_fraction = len(deleted) / len(previous_codes)
    if global_fraction > max_fraction:
        errors.append(
            f"global regression {global_fraction:.3f} exceeds {max_fraction:.3f}"
        )
    max_global = _positive_int(
        context.get("maxGlobalDeletions"), DEFAULT_MAX_GLOBAL_DELETIONS
    )
    if len(deleted) > max_global:
        errors.append(
            f"global deletion count {len(deleted)} exceeds {max_global}"
        )

    deleted_by_series: Dict[str, int] = {}
    previous_by_series: Dict[str, int] = {}
    for code in previous_codes:
        series = code.rsplit("-", 1)[0]
        previous_by_series[series] = previous_by_series.get(series, 0) + 1
    for code in deleted:
        series = code.rsplit("-", 1)[0]
        deleted_by_series[series] = deleted_by_series.get(series, 0) + 1
    max_series = _positive_int(
        context.get("maxSeriesDeletions"), DEFAULT_MAX_SERIES_DELETIONS
    )
    for series in sorted(deleted_by_series):
        count = deleted_by_series[series]
        fraction = count / previous_by_series[series]
        if fraction > max_fraction:
            errors.append(
                f"series {series} regression {fraction:.3f} exceeds {max_fraction:.3f}"
            )
        if count > max_series:
            errors.append(
                f"series {series} deletion count {count} exceeds {max_series}"
            )
    return errors


def _video_map(catalog: Optional[Mapping[str, object]]) -> Dict[str, dict]:
    result = {}
    if not isinstance(catalog, Mapping):
        return result
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if isinstance(video, Mapping) and isinstance(video.get("code"), str):
                result[video["code"]] = dict(video)
    return result


def _metadata(video: Mapping[str, object]) -> str:
    return json.dumps(
        {key: value for key, value in video.items() if key != "links"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _product_id_map(videos: Mapping[str, dict]) -> Dict[int, str]:
    result = {}
    for code, video in videos.items():
        product_id = video.get("productId")
        if isinstance(product_id, int) and not isinstance(product_id, bool) and product_id > 0:
            result[product_id] = code
    return result


def _in_audit_scope(
    video: Mapping[str, object], context: Mapping[str, object]
) -> bool:
    product_id = video.get("productId")
    start_id = context.get("startId")
    end_id = context.get("endId")
    if start_id is not None and (
        not isinstance(product_id, int) or product_id < int(start_id)
    ):
        return False
    if end_id is not None and (
        not isinstance(product_id, int) or product_id > int(end_id)
    ):
        return False
    release_date = video.get("releaseDate")
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


def _has_link_leaf(links: Mapping[str, object]) -> bool:
    for value in links.values():
        if isinstance(value, Mapping):
            if _has_link_leaf(value):
                return True
        elif isinstance(value, str) and value:
            return True
    return False


def _valid_http_url(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_drive_url(value: object) -> bool:
    if not _valid_http_url(value):
        return False
    parsed = urlparse(str(value))
    if parsed.hostname != "drive.google.com":
        return False
    if re.fullmatch(r"/drive/folders/[A-Za-z0-9_-]+/?", parsed.path):
        return True
    if re.fullmatch(r"/file/d/[A-Za-z0-9_-]+(?:/[^?#]*)?", parsed.path):
        return True
    if parsed.path != "/open":
        return False
    identifiers = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
    return len(identifiers) == 1 and bool(
        re.fullmatch(r"[A-Za-z0-9_-]+", identifiers[0])
    )


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_generated_at(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _has_mojibake(value: str) -> bool:
    if any(marker in value for marker in _MOJIBAKE_MARKERS):
        return True
    return any(value.count(marker) >= 2 for marker in _REPEATED_MOJIBAKE_CHARS)


def _fraction(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0 <= parsed <= 1 else default


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default
