#!/usr/bin/env python3
"""Merge catalog or resolved-link changes into the public 30-day changelog."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.link_updates import (  # noqa: E402
    diff_catalog_links,
    diff_resolved_links,
    merge_link_updates,
)
from src.giga_catalog.resolved_links import atomic_write_json, load_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("catalog", "resolved"), required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--at")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    before = load_json(args.before, {})
    after = load_json(args.after, {})
    history = load_json(args.history, {})
    at = args.at or (after.get("generatedAt") if isinstance(after, dict) else None)
    if not isinstance(at, str):
        at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    diff = diff_catalog_links if args.source == "catalog" else diff_resolved_links
    events = diff(before, after, at=at)
    merged = merge_link_updates(history, events)
    current = None
    try:
        current = json.loads(args.history.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    if merged != current:
        atomic_write_json(args.history, merged)
    print(json.dumps({"source": args.source, "changes": len(events)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
