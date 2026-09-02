"""Deterministic state and public-manifest helpers for resolved landing pages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit


PROVIDER_ORDER = ("reupload", "streamtape", "player4me", "gofile")
SOURCE_HOST = "ouo.io"
GOFILE_HOSTS = {"gofile.io", "www.gofile.io"}
STREAMTAPE_HOSTS = {"streamtape.com"}
PLAYER4ME_HOSTS = {"gigaandzen.embed4me.com"}
ALLOWED_FINAL_HOSTS = GOFILE_HOSTS | STREAMTAPE_HOSTS | PLAYER4ME_HOSTS
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SLOT_RE = re.compile(
    r"^(standard|uncensored)\.(reupload|streamtape|player4me|gofile)$"
)
GOFILE_PATH_RE = re.compile(r"^/d/[A-Za-z0-9]+/?$")
STREAMTAPE_PATH_RE = re.compile(r"^/(?:v|e)/[A-Za-z0-9_-]+(?:/[^/?#]*)?/?$")


@dataclass(frozen=True)
class LinkCandidate:
    code: str
    slot: str
    provider: str
    source_url: str
    source_url_hash: str
    release_date: str = ""

    @property
    def key(self) -> str:
        return f"{self.code}\0{self.slot}"


def source_url_hash(source_url: str) -> str:
    return "sha256:" + hashlib.sha256(source_url.encode("utf-8")).hexdigest()


def _valid_source_url(value: object) -> Optional[str]:
    if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != SOURCE_HOST
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[A-Za-z0-9]+", parsed.path)
    ):
        return None
    return value


def iter_catalog_candidates(catalog: Mapping[str, object]) -> Iterator[LinkCandidate]:
    candidates = []
    for series in catalog.get("series", []) if isinstance(catalog, Mapping) else []:
        if not isinstance(series, Mapping):
            continue
        for video in series.get("videos", []):
            if not isinstance(video, Mapping):
                continue
            code = str(video.get("code") or "").strip().upper()
            release_date = str(video.get("releaseDate") or "")
            links = video.get("links")
            if not code or not isinstance(links, Mapping):
                continue
            for group, source in (
                ("standard", links),
                ("uncensored", links.get("uncensored")),
            ):
                if not isinstance(source, Mapping):
                    continue
                for provider in PROVIDER_ORDER:
                    source_url = _valid_source_url(source.get(provider))
                    if source_url:
                        candidates.append(LinkCandidate(
                            code=code,
                            slot=f"{group}.{provider}",
                            provider=provider,
                            source_url=source_url,
                            source_url_hash=source_url_hash(source_url),
                            release_date=release_date,
                        ))
    candidates.sort(key=lambda item: item.release_date, reverse=True)
    return iter(candidates)


def validate_final_url(value: object, *, expected_provider: Optional[str] = None) -> Optional[str]:
    if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_FINAL_HOSTS
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
    ):
        return None
    if host in GOFILE_HOSTS and not GOFILE_PATH_RE.fullmatch(parsed.path):
        return None
    if host in STREAMTAPE_HOSTS and not STREAMTAPE_PATH_RE.fullmatch(parsed.path):
        return None
    if host in PLAYER4ME_HOSTS and (
        parsed.path not in ("", "/") or not re.fullmatch(r"[A-Za-z0-9]+", parsed.fragment)
    ):
        return None
    if host not in PLAYER4ME_HOSTS and parsed.fragment:
        return None
    actual_provider = (
        "gofile" if host in GOFILE_HOSTS
        else "streamtape" if host in STREAMTAPE_HOSTS
        else "player4me"
    )
    if expected_provider is not None and actual_provider != expected_provider:
        return None
    return urlunsplit(("https", host, parsed.path, "", parsed.fragment))


def provider_for_final_url(value: object) -> Optional[str]:
    normalized = validate_final_url(value)
    if not normalized:
        return None
    host = (urlsplit(normalized).hostname or "").lower()
    if host in GOFILE_HOSTS:
        return "gofile"
    if host in STREAMTAPE_HOSTS:
        return "streamtape"
    if host in PLAYER4ME_HOSTS:
        return "player4me"
    return None


def build_manifest(
    candidates: Iterable[LinkCandidate],
    state: Mapping[str, object],
    *,
    generated_at: str,
    previous_manifest: Optional[Mapping[str, object]] = None,
) -> dict:
    results = state.get("results", {}) if isinstance(state, Mapping) else {}
    if not isinstance(results, Mapping):
        results = {}
    entries = {}
    for candidate in sorted(candidates, key=lambda item: (item.code, item.slot)):
        result = results.get(candidate.key)
        if not isinstance(result, Mapping):
            continue
        provider = result.get("provider") or provider_for_final_url(result.get("finalUrl"))
        final_url = validate_final_url(result.get("finalUrl"), expected_provider=provider)
        checked_at = result.get("checkedAt")
        if (
            result.get("status") != "verified"
            or result.get("sourceUrlHash") != candidate.source_url_hash
            or provider not in PROVIDER_ORDER
            or not final_url
            or not isinstance(checked_at, str)
            or not checked_at
        ):
            continue
        entries.setdefault(candidate.code, {})[candidate.slot] = {
            "provider": provider,
            "sourceUrlHash": candidate.source_url_hash,
            "finalUrl": final_url,
            "kind": "external",
            "status": "verified",
            "checkedAt": checked_at,
        }
    if (
        isinstance(previous_manifest, Mapping)
        and previous_manifest.get("schemaVersion") == 2
        and previous_manifest.get("entries") == entries
        and isinstance(previous_manifest.get("generatedAt"), str)
        and previous_manifest.get("generatedAt")
    ):
        generated_at = previous_manifest["generatedAt"]
    return {
        "schemaVersion": 2,
        "generatedAt": generated_at,
        "entries": entries,
    }


def seed_state_from_manifest(
    candidates: Iterable[LinkCandidate],
    manifest: Mapping[str, object],
    state: Mapping[str, object],
) -> dict:
    seeded = dict(state) if isinstance(state, Mapping) else {}
    existing_results = seeded.get("results")
    results = dict(existing_results) if isinstance(existing_results, Mapping) else {}
    entries = manifest.get("entries", {}) if isinstance(manifest, Mapping) and manifest.get("schemaVersion") == 2 else {}
    if not isinstance(entries, Mapping):
        entries = {}
    for candidate in candidates:
        if candidate.key in results:
            continue
        code_entries = entries.get(candidate.code)
        entry = code_entries.get(candidate.slot) if isinstance(code_entries, Mapping) else None
        provider = entry.get("provider") if isinstance(entry, Mapping) else None
        final_url = validate_final_url(entry.get("finalUrl"), expected_provider=provider) if isinstance(entry, Mapping) else None
        if (
            isinstance(entry, Mapping)
            and provider in PROVIDER_ORDER
            and entry.get("sourceUrlHash") == candidate.source_url_hash
            and entry.get("status") == "verified"
            and entry.get("kind") == "external"
            and final_url
            and isinstance(entry.get("checkedAt"), str)
        ):
            results[candidate.key] = {
                "sourceUrlHash": candidate.source_url_hash,
                "status": "verified",
                "provider": provider,
                "finalUrl": final_url,
                "checkedAt": entry["checkedAt"],
                "attempts": 0,
            }
    seeded["schemaVersion"] = 1
    seeded["results"] = results
    return seeded


def atomic_write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_json(path: Path, default: object) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
