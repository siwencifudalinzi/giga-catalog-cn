"""Add verified archived GIGA titles and AsiaMonstr preview URLs to the catalog."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
import sys

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.giga_catalog.archived_import import (  # noqa: E402
    build_archived_products,
    merge_archived_catalog,
    parse_archived_sheet_links,
)
from src.giga_catalog.merge import serialize_catalog  # noqa: E402
from src.giga_catalog.validation import validate_catalog  # noqa: E402


DEFAULT_AUDIT = REPOSITORY_ROOT / "audits" / "2026-09-12-giga-missing-records"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--catalog", type=Path, default=REPOSITORY_ROOT / "public" / "data" / "catalog.json")
    parser.add_argument("--dry-run", action="store_true")
    options = parser.parse_args()

    with (options.audit_dir / "confirmed-delisted.csv").open(encoding="utf-8-sig", newline="") as handle:
        confirmed = list(csv.DictReader(handle))
    detail_payload = json.loads(
        (options.audit_dir / "evidence" / "asiamonstr-delisted-details.json").read_text(encoding="utf-8-sig")
    )
    details = detail_payload.get("records", {})
    if len(confirmed) != 288 or len(details) != len(confirmed):
        raise ValueError(f"incomplete archived input: confirmed={len(confirmed)}, details={len(details)}")
    failures = sorted(
        code for code, record in details.items()
        if record.get("status") != "ok" or not record.get("previewImages")
    )
    if failures:
        raise ValueError("missing AsiaMonstr previews: " + ", ".join(failures))

    products = build_archived_products(confirmed, details)
    if len(products) != len(confirmed):
        raise ValueError(f"only {len(products)} of {len(confirmed)} archived products are valid")
    sheet_text = (options.audit_dir / "evidence" / "sheet.csv").read_text(encoding="utf-8-sig")
    links = parse_archived_sheet_links(sheet_text, {item["code"] for item in products})
    previous = json.loads(options.catalog.read_text(encoding="utf-8-sig"))
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    catalog, summary = merge_archived_catalog(previous, products, links, generated_at=generated_at)
    errors = validate_catalog(catalog, previous, mode="incremental")
    if errors:
        raise ValueError("catalog validation failed:\n" + "\n".join(errors))

    result = {
        "added": summary["counts"]["added"],
        "archivedProducts": len(products),
        "linkedArchivedProducts": len(links),
        "previewImages": sum(len(item["previewImages"]) for item in products),
        "totalVideos": catalog["totals"]["videos"],
    }
    if not options.dry_run:
        output = serialize_catalog(catalog)
        temporary = options.catalog.with_name(f".{options.catalog.name}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(output)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(options.catalog))
        finally:
            if temporary.exists():
                temporary.unlink()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
