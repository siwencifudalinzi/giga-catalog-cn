"""Apply completed JAVRyo crawl results to catalog and resolved-link artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_runtime_catalog import build_runtime_from_catalog  # noqa: E402
from src.giga_catalog.javryo import apply_overlay_to_catalog, build_manifest_entries  # noqa: E402
from src.giga_catalog.merge import serialize_catalog  # noqa: E402
from src.giga_catalog.resolved_links import atomic_write_json  # noqa: E402
from src.giga_catalog.validation import validate_stored_catalog  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def javryo_manifest_changed(before: dict, after: dict) -> bool:
    def selected(entries):
        return {code: slots["standard.javryo"] for code, slots in entries.items()
                if isinstance(slots, dict) and "standard.javryo" in slots}
    return selected(before) != selected(after)


def apply_files(root: Path = ROOT) -> dict:
    overlay_path = root / "data/javryo-links.json"
    catalog_path = root / "public/data/catalog.json"
    manifest_path = root / "public/data/resolved-links.json"
    overlay = _load(overlay_path, {})
    catalog = _load(catalog_path, {})
    if not isinstance(catalog, dict):
        raise RuntimeError("catalog is not an object")
    changed = apply_overlay_to_catalog(catalog, overlay)
    if changed:
        generated_at = overlay.get("generatedAt") if isinstance(overlay, dict) else None
        catalog["generatedAt"] = generated_at if isinstance(generated_at, str) else _now()
    manifest = _load(manifest_path, {})
    old_entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}
    entries = {}
    if isinstance(old_entries, dict):
        for code, slots in old_entries.items():
            if not isinstance(slots, dict):
                continue
            kept = {slot: value for slot, value in slots.items() if slot != "standard.javryo"}
            if kept:
                entries[code] = kept
    javryo_entries = build_manifest_entries(
        overlay,
        _load(root / "data/state/javryo-embed-verification.json", {}),
        _load(root / "data/state/javryo-streamtape-verification.json", {}),
        _load(root / "data/javryo-embeds.json", {}),
    )
    for code, slots in javryo_entries.items():
        entries.setdefault(code, {}).update(slots)
    if not changed and javryo_manifest_changed(old_entries, entries):
        catalog["generatedAt"] = _now()
    errors = validate_stored_catalog(catalog)
    if errors:
        raise RuntimeError("catalog validation failed:\n" + "\n".join(sorted(errors)))
    _write_bytes(catalog_path, serialize_catalog(catalog))
    atomic_write_json(manifest_path, {
        "schemaVersion": 2,
        "generatedAt": _now(),
        "entries": {code: entries[code] for code in sorted(entries)},
    })
    runtime = build_runtime_from_catalog(
        catalog_path,
        root / "public/data/catalog-core.json",
        root / "public/data/catalog-tags.json",
        root / "public/data/catalog-bootstrap.json",
        root / "public/data/runtime",
    )
    return {
        "catalogLinksAdded": changed,
        "javryoPages": len(overlay.get("entries", {})) if isinstance(overlay, dict) else 0,
        "verifiedStreamtape": sum(slots["standard.javryo"]["provider"] == "streamtape"
                                   for slots in javryo_entries.values()),
        "verifiedJavryoStreams": sum(slots["standard.javryo"]["provider"] == "javryo_stream"
                                       for slots in javryo_entries.values()),
        "runtime": runtime,
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(apply_files(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
