#!/usr/bin/env python3
"""Build or inspect the resolved-link queue and public manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.resolved_links import (  # noqa: E402
    atomic_write_json,
    build_manifest,
    iter_catalog_candidates,
    load_json,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "public/data/catalog.json")
    parser.add_argument("--state", type=Path, default=ROOT / "data/state/resolved-links-state.json")
    parser.add_argument("--output", type=Path, default=ROOT / "public/data/resolved-links.json")
    parser.add_argument("--write", action="store_true", help="Atomically write the public manifest")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    catalog = load_json(args.catalog, {})
    candidates = list(iter_catalog_candidates(catalog))
    state = load_json(args.state, {"schemaVersion": 1, "results": {}})
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(candidates, state, generated_at=generated_at)
    resolved = sum(len(slots) for slots in manifest["entries"].values())
    print(f"candidates={len(candidates)} resolved={resolved} pending={len(candidates) - resolved}")
    if args.write:
        atomic_write_json(args.output, manifest)
        print(f"wrote={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
