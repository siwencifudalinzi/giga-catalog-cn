"""Build compact browser runtime artifacts from the complete catalog."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping, Tuple


TAG_VIDEO_FIELDS = (
    "tagIds",
    "tagsStatus",
    "tagsUpdatedAt",
    "tagsSource",
)

_SERIES_CODE_RE = re.compile(r"[A-Z0-9]+")
_GENERATION_TOKEN = "{generation}"


@dataclass(frozen=True)
class RuntimeV3Bundle:
    generation: str
    bootstrap: dict
    files: tuple[tuple[str, dict], ...]


def build_runtime_catalogs(catalog: Mapping[str, object]) -> Tuple[dict, dict]:
    """Return an immutable-source core catalog and compact tag payload."""
    core = copy.deepcopy(dict(catalog))
    tags = core.pop("tags", [])
    assignments = []
    for series in core.get("series", []):
        for video in series.get("videos", []):
            tag_ids = video.get("tagIds", [])
            if tag_ids:
                assignments.append([str(video.get("code") or ""), copy.deepcopy(tag_ids)])
            for field in TAG_VIDEO_FIELDS:
                video.pop(field, None)
    assignments.sort(key=lambda item: item[0])
    tag_payload = {
        "schemaVersion": 1,
        "generatedAt": copy.deepcopy(catalog.get("generatedAt")),
        "tags": copy.deepcopy(tags),
        "assignments": assignments,
    }
    return core, tag_payload


def build_runtime_v3(
    catalog: Mapping[str, object], *, recent_limit: int = 24
) -> RuntimeV3Bundle:
    """Build deterministic, generation-scoped runtime artifacts."""
    if (
        isinstance(recent_limit, bool)
        or not isinstance(recent_limit, int)
        or recent_limit <= 0
    ):
        raise ValueError("recent_limit must be a positive integer")
    templates = _build_v3_templates(catalog, recent_limit=recent_limit)
    identity = hashlib.sha256(_compact_bytes(templates)).hexdigest()
    bootstrap, files = _bind_generation(templates, identity)
    _validate_runtime_v3(catalog, bootstrap, files)
    return RuntimeV3Bundle(identity, bootstrap, tuple(files))


def _compact_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _core_video(video: Mapping[str, object]) -> dict:
    """Copy a video while keeping tag data in the dedicated V3 tag artifact."""
    core = copy.deepcopy(dict(video))
    for field in TAG_VIDEO_FIELDS:
        core.pop(field, None)
    return core


def _build_v3_templates(
    catalog: Mapping[str, object], *, recent_limit: int
) -> dict:
    if not isinstance(catalog, Mapping):
        raise ValueError("catalog must be an object")
    source_series = catalog.get("series")
    if not isinstance(source_series, list):
        raise ValueError("series must be an array")

    seen_series = set()
    seen_videos = set()
    series_records = []
    search_videos = []
    assignments = []
    all_videos = []

    for source in source_series:
        if not isinstance(source, Mapping):
            raise ValueError("series entries must be objects")
        code = source.get("code")
        if not isinstance(code, str) or _SERIES_CODE_RE.fullmatch(code) is None:
            raise ValueError(f"unsafe series code: {code}")
        if code in seen_series:
            raise ValueError(f"duplicate series code: {code}")
        seen_series.add(code)

        source_videos = source.get("videos")
        if not isinstance(source_videos, list):
            raise ValueError(f"{code} videos must be an array")
        count = source.get("count")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count != len(source_videos)
        ):
            raise ValueError(f"count mismatch for {code}")

        videos = []
        for source_video in source_videos:
            if not isinstance(source_video, Mapping):
                raise ValueError(f"{code} videos must contain objects")
            video_code = source_video.get("code")
            if not isinstance(video_code, str) or not video_code:
                raise ValueError(f"invalid video code in {code}")
            if video_code in seen_videos:
                raise ValueError(f"duplicate video code: {video_code}")
            seen_videos.add(video_code)
            core = _core_video(source_video)
            videos.append(core)
            tagged = source_video.get("tagIds", [])
            if tagged:
                assignments.append([video_code, copy.deepcopy(tagged)])
            indexed = copy.deepcopy(core)
            indexed["series"] = code
            search_videos.append(indexed)
            all_videos.append((code, source_video, core))

        videos.sort(key=lambda item: (item.get("number"), item.get("code")))
        summary = {
            "code": code,
            "count": count,
            "firstReleaseDate": copy.deepcopy(source.get("firstReleaseDate")),
            "latestReleaseDate": copy.deepcopy(source.get("latestReleaseDate")),
        }
        if "links" in source:
            summary["links"] = copy.deepcopy(source["links"])
        series_records.append(
            {
                "summary": summary,
                "videos": videos,
            }
        )

    series_records.sort(key=lambda item: item["summary"]["code"])
    series_records.sort(
        key=lambda item: item["summary"]["latestReleaseDate"], reverse=True
    )
    search_videos.sort(key=lambda item: item["code"])
    assignments.sort(key=lambda item: item[0])
    recent_order = sorted(all_videos, key=lambda item: item[1].get("code"))
    recent_order.sort(
        key=lambda item: item[1].get("releaseDate"), reverse=True
    )
    recent_videos = [
        item[2] | {"series": item[0]} for item in recent_order
    ]
    recent_videos = recent_videos[:recent_limit]

    bootstrap_series = []
    shard_templates = []
    for record in series_records:
        summary = copy.deepcopy(record["summary"])
        summary["artifact"] = (
            f"runtime/g/{_GENERATION_TOKEN}/series/{summary['code'].lower()}.json"
        )
        bootstrap_series.append(summary)
        shard_templates.append(
            (
                f"runtime/g/{_GENERATION_TOKEN}/series/{record['summary']['code'].lower()}.json",
                {
                    "schemaVersion": 3,
                    "generation": _GENERATION_TOKEN,
                    "generatedAt": copy.deepcopy(catalog.get("generatedAt")),
                    "series": {
                        **copy.deepcopy(record["summary"]),
                        "videos": copy.deepcopy(record["videos"]),
                    },
                },
            )
        )

    return {
        "bootstrap": {
            "schemaVersion": 3,
            "generation": _GENERATION_TOKEN,
            "generatedAt": copy.deepcopy(catalog["generatedAt"]),
            "totals": copy.deepcopy(catalog["totals"]),
            "refresh": copy.deepcopy(catalog["refresh"]),
            "resources": copy.deepcopy(catalog.get("resources", {})),
            "artifacts": {
                "search": f"runtime/g/{_GENERATION_TOKEN}/search.json",
                "tags": f"runtime/g/{_GENERATION_TOKEN}/tags.json",
            },
            "recentVideos": recent_videos,
            "series": bootstrap_series,
        },
        "files": [
            (
                f"runtime/g/{_GENERATION_TOKEN}/search.json",
                {
                    "schemaVersion": 3,
                    "generation": _GENERATION_TOKEN,
                    "generatedAt": copy.deepcopy(catalog.get("generatedAt")),
                    "videos": search_videos,
                },
            ),
            (
                f"runtime/g/{_GENERATION_TOKEN}/tags.json",
                {
                    "schemaVersion": 3,
                    "generation": _GENERATION_TOKEN,
                    "generatedAt": copy.deepcopy(catalog.get("generatedAt")),
                    "tags": copy.deepcopy(catalog.get("tags", [])),
                    "assignments": assignments,
                },
            ),
            *shard_templates,
        ],
    }


def _bind_generation(templates: Mapping[str, object], identity: str):
    bootstrap = copy.deepcopy(templates["bootstrap"])
    bootstrap["generation"] = identity
    bootstrap["artifacts"] = {
        "search": f"runtime/g/{identity}/search.json",
        "tags": f"runtime/g/{identity}/tags.json",
    }
    for summary in bootstrap.get("series", []):
        if isinstance(summary, dict) and isinstance(summary.get("artifact"), str):
            summary["artifact"] = summary["artifact"].replace(
                _GENERATION_TOKEN, identity
            )
    files = []
    for path, payload in templates["files"]:
        bound_path = path.replace(_GENERATION_TOKEN, identity)
        bound_payload = copy.deepcopy(payload)
        bound_payload["generation"] = identity
        files.append((bound_path, bound_payload))
    return bootstrap, files


def _validate_runtime_v3(
    catalog: Mapping[str, object], bootstrap: Mapping[str, object], files
) -> None:
    if bootstrap.get("schemaVersion") != 3:
        raise ValueError("bootstrap schemaVersion mismatch")
    generation = bootstrap.get("generation")
    if not isinstance(generation, str) or re.fullmatch(r"[0-9a-f]{64}", generation) is None:
        raise ValueError("invalid generation")
    generated_at = catalog.get("generatedAt")
    if bootstrap.get("generatedAt") != generated_at:
        raise ValueError("generatedAt mismatch")

    expected_series = catalog.get("series", [])
    if not isinstance(expected_series, list):
        raise ValueError("series must be an array")
    expected_by_code = {}
    expected_video_by_code = {}
    linked_videos = 0
    for source in expected_series:
        if not isinstance(source, Mapping):
            raise ValueError("series entries must be objects")
        code = source.get("code")
        if not isinstance(code, str) or _SERIES_CODE_RE.fullmatch(code) is None:
            raise ValueError(f"unsafe series code: {code}")
        if code in expected_by_code:
            raise ValueError(f"duplicate series code: {code}")
        videos = source.get("videos")
        if not isinstance(videos, list) or source.get("count") != len(videos):
            raise ValueError(f"count mismatch for {code}")
        expected_by_code[code] = source
        for video in videos:
            if not isinstance(video, Mapping):
                raise ValueError(f"{code} videos must contain objects")
            video_code = video.get("code")
            if video_code in expected_video_by_code:
                raise ValueError(f"duplicate video code: {video_code}")
            expected_video_by_code[video_code] = (code, video)
            if _has_link_leaf(video.get("links")):
                linked_videos += 1

    expected_totals = {
        "series": len(expected_series),
        "videos": len(expected_video_by_code),
        "linkedVideos": linked_videos,
    }
    if bootstrap.get("totals") != catalog.get("totals"):
        raise ValueError("totals mismatch")
    if any(bootstrap["totals"].get(key) != value for key, value in expected_totals.items()):
        raise ValueError("bad totals")

    prefix = f"runtime/g/{generation}/"
    expected_paths = {
        prefix + "search.json",
        prefix + "tags.json",
        *(
            prefix + f"series/{code.lower()}.json"
            for code in expected_by_code
        ),
    }
    paths = [path for path, _ in files]
    if len(paths) != len(set(paths)) or set(paths) != expected_paths:
        raise ValueError("runtime artifact paths mismatch")

    payloads = dict(files)
    for path, payload in files:
        if payload.get("schemaVersion") != 3:
            raise ValueError("runtime payload schemaVersion mismatch")
        if payload.get("generation") != generation:
            raise ValueError("runtime payload generation mismatch")
        if payload.get("generatedAt") != generated_at:
            raise ValueError("runtime payload generatedAt mismatch")

    search = payloads[prefix + "search.json"]
    search_by_code = {}
    for item in search.get("videos", []):
        if not isinstance(item, Mapping) or item.get("code") in search_by_code:
            raise ValueError("duplicate search video code")
        code = item.get("code")
        expected = expected_video_by_code.get(code)
        if expected is None or item.get("series") != expected[0]:
            raise ValueError("search video reference mismatch")
        expected_core = _core_video(expected[1]) | {"series": expected[0]}
        if dict(item) != expected_core:
            raise ValueError(f"search video mismatch: {code}")
        search_by_code[code] = item
    if set(search_by_code) != set(expected_video_by_code):
        raise ValueError("search coverage mismatch")

    tags_payload = payloads[prefix + "tags.json"]
    if tags_payload.get("tags") != catalog.get("tags", []):
        raise ValueError("tag definitions mismatch")
    expected_assignments = sorted(
        [
            [code, copy.deepcopy(video.get("tagIds", []))]
            for code, (_, video) in expected_video_by_code.items()
            if video.get("tagIds", [])
        ],
        key=lambda item: item[0],
    )
    if tags_payload.get("assignments") != expected_assignments:
        raise ValueError("tag assignments mismatch")

    bootstrap_summaries = {item.get("code"): item for item in bootstrap.get("series", [])}
    if set(bootstrap_summaries) != set(expected_by_code):
        raise ValueError("series summary coverage mismatch")
    for code, source in expected_by_code.items():
        summary = bootstrap_summaries[code]
        for field in ("code", "count", "firstReleaseDate", "latestReleaseDate"):
            if summary.get(field) != source.get(field):
                raise ValueError(f"series summary mismatch: {code}")
        if ("links" in source) != ("links" in summary):
            raise ValueError(f"series links mismatch: {code}")
        if "links" in source and summary["links"] != source["links"]:
            raise ValueError(f"series links mismatch: {code}")
        expected_artifact = prefix + f"series/{code.lower()}.json"
        if summary.get("artifact") != expected_artifact:
            raise ValueError(f"series artifact mismatch: {code}")
        shard = payloads[expected_artifact]["series"]
        shard_videos = shard.get("videos")
        if shard.get("code") != code or shard.get("count") != len(shard_videos or []):
            raise ValueError(f"series shard count mismatch: {code}")
        shard_codes = [video.get("code") for video in shard_videos or []]
        expected_codes = [video.get("code") for video in source.get("videos", [])]
        if set(shard_codes) != set(expected_codes) or len(shard_codes) != len(set(shard_codes)):
            raise ValueError(f"series shard coverage mismatch: {code}")
        for video in shard_videos:
            expected = expected_video_by_code.get(video.get("code"))
            if expected is None or expected[0] != code or dict(video) != _core_video(expected[1]):
                raise ValueError(f"series shard video mismatch: {video.get('code')}")

    expected_recent = [
        _core_video(video) | {"series": series_code}
        for code, (series_code, video) in sorted(
            expected_video_by_code.items(), key=lambda item: item[0]
        )
    ]
    expected_recent.sort(
        key=lambda item: expected_video_by_code[item["code"]][1].get(
            "releaseDate"
        ),
        reverse=True,
    )
    if bootstrap.get("recentVideos") != expected_recent[: len(bootstrap.get("recentVideos", []))]:
        raise ValueError("recent video ordering mismatch")


def _has_link_leaf(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_has_link_leaf(child) for child in value.values())
    return isinstance(value, str) and bool(value)
