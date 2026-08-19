"""Refresh the at-most-six locally hosted covers used on the homepage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.featured_covers import cache_featured_covers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=ROOT / "public" / "data" / "catalog.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "public" / "media" / "featured-covers")
    parser.add_argument("--manifest", type=Path, default=ROOT / "public" / "data" / "featured-covers.json")
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    result = cache_featured_covers(args.catalog, args.output_dir, args.manifest, retries=args.retries)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
