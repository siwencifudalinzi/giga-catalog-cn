"""Build a small public changelog without publishing link targets."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping


PROVIDERS = ("reupload", "streamtape", "player4me", "vidara", "gofile")
SOURCES = {"catalog", "resolved"}
ACTIONS = {"added", "updated", "removed", "resolved"}


def _flatten_catalog(catalog: object) -> dict[tuple[str, str], str]:
    flattened: dict[tuple[str, str], str] = {}
    if not isinstance(catalog, Mapping):
        return flattened
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if not isinstance(video, Mapping):
                continue
            code = str(video.get("code") or "").strip().upper()
            links = video.get("links")
            if not code or not isinstance(links, Mapping):
                continue
            for group, values in (("standard", links), ("uncensored", links.get("uncensored"))):
                if not isinstance(values, Mapping):
                    continue
                for provider in PROVIDERS:
                    value = values.get(provider)
                    if isinstance(value, str) and value:
                        flattened[(code, f"{group}.{provider}")] = value
    return flattened


def _event(*, at: str, source: str, code: str, slot: str, action: str, provider: str) -> dict:
    identity = "\0".join((at, source, code, slot, action, provider))
    return {
        "id": "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "at": at,
        "source": source,
        "code": code,
        "slot": slot,
        "action": action,
        "provider": provider,
    }


def diff_catalog_links(before: object, after: object, *, at: str) -> list[dict]:
    old = _flatten_catalog(before)
    new = _flatten_catalog(after)
    events = []
    for code, slot in sorted(old.keys() | new.keys()):
        previous, current = old.get((code, slot)), new.get((code, slot))
        if previous == current:
            continue
        action = "added" if previous is None else "removed" if current is None else "updated"
        events.append(_event(
            at=at,
            source="catalog",
            code=code,
            slot=slot,
            action=action,
            provider=slot.rsplit(".", 1)[-1],
        ))
    return events


def _verified_entries(manifest: object) -> dict[tuple[str, str], tuple]:
    verified = {}
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("entries"), Mapping):
        return verified
    for raw_code, slots in manifest["entries"].items():
        code = str(raw_code).strip().upper()
        if not code or not isinstance(slots, Mapping):
            continue
        for slot, entry in slots.items():
            if (
                isinstance(slot, str)
                and isinstance(entry, Mapping)
                and entry.get("status") == "verified"
                and entry.get("kind") == "external"
                and entry.get("provider") in PROVIDERS
            ):
                verified[(code, slot)] = (
                    entry.get("provider"),
                    entry.get("sourceUrlHash"),
                    entry.get("finalUrl"),
                )
    return verified


def diff_resolved_links(before: object, after: object, *, at: str) -> list[dict]:
    old = _verified_entries(before)
    new = _verified_entries(after)
    events = []
    for (code, slot), current in sorted(new.items()):
        if old.get((code, slot)) == current:
            continue
        events.append(_event(
            at=at,
            source="resolved",
            code=code,
            slot=slot,
            action="resolved",
            provider=str(current[0]),
        ))
    return events


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_entry(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "id", "at", "source", "code", "slot", "action", "provider"
    }:
        return False
    return (
        isinstance(value.get("id"), str)
        and re.fullmatch(r"[A-Za-z0-9:._-]{1,96}", value["id"]) is not None
        and _parse_time(value.get("at")) is not None
        and value.get("source") in SOURCES
        and isinstance(value.get("code"), str)
        and re.fullmatch(r"[A-Z0-9]+-[0-9]+", value["code"]) is not None
        and isinstance(value.get("slot"), str)
        and re.fullmatch(
            r"(?:standard|uncensored)\.(?:reupload|streamtape|player4me|vidara|gofile)",
            value["slot"],
        ) is not None
        and value.get("action") in ACTIONS
        and value.get("provider") in PROVIDERS
    )


def merge_link_updates(
    history: object,
    events: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
    retention_days: int = 30,
    maximum_entries: int = 5000,
) -> dict:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current_time - timedelta(days=retention_days)
    candidates = []
    if isinstance(history, Mapping) and isinstance(history.get("entries"), list):
        candidates.extend(history["entries"])
    candidates.extend(events)
    by_id = {}
    for item in candidates:
        if _safe_entry(item) and _parse_time(item["at"]) >= cutoff:
            by_id[item["id"]] = dict(item)
    entries = sorted(by_id.values(), key=lambda item: (item["at"], item["id"]), reverse=True)
    entries = entries[:maximum_entries]
    generated_at = entries[0]["at"] if entries else current_time.isoformat().replace("+00:00", "Z")
    return {"schemaVersion": 1, "generatedAt": generated_at, "entries": entries}
