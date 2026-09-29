"""Convert the completed private API audit to a deterministic stable candidate inventory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo_streams import collect_embed_candidates  # noqa: E402
from src.giga_catalog.resolved_links import atomic_write_json  # noqa: E402


def main() -> None:
    crawl = json.loads((ROOT / "data/state/javryo-crawl.json").read_text(encoding="utf-8"))
    audit = json.loads((ROOT / "data/state/javryo-embed-audit.json").read_text(encoding="utf-8"))
    candidates = collect_embed_candidates(crawl, audit)
    if len(candidates) != 1923:
        raise RuntimeError(f"Expected 1923 JAVRyo candidates, got {len(candidates)}")
    atomic_write_json(ROOT / "data/javryo-embeds.json", {
        "schemaVersion": 1, "entries": {item.code: item.public_record() for item in candidates},
    })
    from collections import Counter
    print(dict(Counter(item.status for item in candidates)))


if __name__ == "__main__":
    main()
