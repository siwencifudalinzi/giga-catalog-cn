"""Build compact browser runtime artifacts from the complete catalog."""

from __future__ import annotations

import copy
from typing import Mapping, Tuple


TAG_VIDEO_FIELDS = (
    "tagIds",
    "tagsStatus",
    "tagsUpdatedAt",
    "tagsSource",
)


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
