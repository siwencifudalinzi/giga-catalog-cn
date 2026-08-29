"""Republish browser runtime artifacts from the checked-in canonical catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.runtime_catalog import build_runtime_catalogs, build_runtime_v3
from src.giga_catalog.validation import validate_stored_catalog
from scripts.refresh import _commit_transaction


_GENERATION_RE = re.compile(r"[0-9a-f]{64}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "public" / "data" / "catalog.json",
    )
    parser.add_argument(
        "--runtime-core",
        type=Path,
        default=ROOT / "public" / "data" / "catalog-core.json",
    )
    parser.add_argument(
        "--runtime-tags",
        type=Path,
        default=ROOT / "public" / "data" / "catalog-tags.json",
    )
    parser.add_argument(
        "--runtime-bootstrap",
        type=Path,
        default=ROOT / "public" / "data" / "catalog-bootstrap.json",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT / "public" / "data" / "runtime",
    )
    return parser


def build_runtime_from_catalog(
    catalog_path: Path,
    runtime_core: Path,
    runtime_tags: Path,
    runtime_bootstrap: Path,
    runtime_root: Path,
) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    errors = validate_stored_catalog(catalog)
    if errors:
        raise RuntimeError("catalog validation failed:\n" + "\n".join(sorted(errors)))
    return publish_runtime_artifacts(
        catalog,
        runtime_core=runtime_core,
        runtime_tags=runtime_tags,
        runtime_bootstrap=runtime_bootstrap,
        runtime_root=runtime_root,
    )


def publish_runtime_artifacts(
    catalog: Mapping[str, object],
    *,
    runtime_core: Path,
    runtime_tags: Path,
    runtime_bootstrap: Path,
    runtime_root: Path,
) -> dict:
    """Atomically publish the derived V1 and V3 runtime artifacts."""
    runtime_core_payload, runtime_tags_payload = build_runtime_catalogs(catalog)
    v3 = build_runtime_v3(catalog)
    previous_generation = read_bootstrap_generation(runtime_bootstrap)
    targets = [
        (runtime_core, _json_bytes(runtime_core_payload)),
        (runtime_tags, _json_bytes(runtime_tags_payload)),
        *[
            (runtime_root.parent / relative, _json_bytes(payload))
            for relative, payload in v3.files
        ],
        (runtime_bootstrap, _json_bytes(v3.bootstrap)),
    ]
    _commit_transaction(targets, replacer=None, stale_remover=None)

    result = {
        "generation": v3.generation,
        "counts": {
            "files": len(v3.files),
            "series": len(catalog.get("series", [])),
            "videos": int(dict(catalog.get("totals", {})).get("videos", 0)),
        },
    }
    try:
        new_generation = read_bootstrap_generation(runtime_bootstrap)
        if new_generation is None:
            raise ValueError("published bootstrap has no valid generation")
        removed = prune_runtime_generations(
            runtime_root,
            keep_generations={new_generation, previous_generation}
            - {None},
        )
        result["prunedGenerations"] = len(removed)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result["pruneError"] = str(error)
    return result


def read_bootstrap_generation(path: Path) -> Optional[str]:
    """Return a valid prior generation, or no generation when none is published."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    generation = payload.get("generation") if isinstance(payload, Mapping) else None
    if not isinstance(generation, str) or _GENERATION_RE.fullmatch(generation) is None:
        return None
    return generation


def prune_runtime_generations(runtime_root: Path, keep_generations: set[str]) -> list[Path]:
    """Remove only obsolete, resolved generation directories below runtime/g."""
    generation_parent = (runtime_root.resolve() / "g").resolve()
    if not generation_parent.is_dir():
        return []
    removed = []
    for candidate in generation_parent.iterdir():
        resolved = candidate.resolve()
        if (
            _GENERATION_RE.fullmatch(candidate.name) is None
            or candidate.name in keep_generations
            or candidate.is_symlink()
            or not resolved.is_dir()
            or resolved.parent != generation_parent
        ):
            continue
        shutil.rmtree(resolved)
        removed.append(candidate)
    return removed


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    options = create_parser().parse_args(argv)
    result = build_runtime_from_catalog(
        options.catalog,
        options.runtime_core,
        options.runtime_tags,
        options.runtime_bootstrap,
        options.runtime_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
