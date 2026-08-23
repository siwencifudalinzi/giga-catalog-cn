#!/usr/bin/env python3
"""Build or inspect the resolved-link queue and public manifest."""

from __future__ import annotations

import argparse
import asyncio
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
    seed_state_from_manifest,
)
from src.giga_catalog.resolved_links_browser import (  # noqa: E402
    PlaywrightOuoResolver,
    collect_candidates,
    collect_candidates_parallel,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "public/data/catalog.json")
    parser.add_argument("--state", type=Path, default=ROOT / "data/state/resolved-links-state.json")
    parser.add_argument("--output", type=Path, default=ROOT / "public/data/resolved-links.json")
    parser.add_argument("--write", action="store_true", help="Atomically write the public manifest")
    parser.add_argument("--browser", action="store_true", help="Collect pending links with persistent Chrome")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-links", type=int, default=0, help="0 processes every pending candidate")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--profile-dir", type=Path, default=ROOT / "data/browser/resolved-links-profile")
    return parser.parse_args(argv)


async def run_browser(args, candidates, state):
    if not 1 <= args.workers <= 8:
        raise SystemExit("--workers must be between 1 and 8")
    resolvers = []
    try:
        for index in range(args.workers):
            profile = args.profile_dir if args.workers == 1 else args.profile_dir.with_name(f"{args.profile_dir.name}-{index + 1}")
            resolvers.append(await PlaywrightOuoResolver.launch(profile, headless=args.headless))

        def checkpoint(current_state):
            atomic_write_json(args.state, current_state)
            generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            atomic_write_json(args.output, build_manifest(candidates, current_state, generated_at=generated_at))

        collector = collect_candidates if len(resolvers) == 1 else collect_candidates_parallel
        resolver_input = resolvers[0] if len(resolvers) == 1 else resolvers
        return await collector(candidates, state, resolver_input, checkpoint=checkpoint, max_links=args.max_links, delay_seconds=args.delay)
    finally:
        await asyncio.gather(*(resolver.close() for resolver in resolvers), return_exceptions=True)


def main(argv=None):
    args = parse_args(argv)
    catalog = load_json(args.catalog, {})
    candidates = list(iter_catalog_candidates(catalog))
    state = load_json(args.state, {"schemaVersion": 1, "results": {}})
    state = seed_state_from_manifest(candidates, load_json(args.output, {}), state)
    if args.browser:
        processed = asyncio.run(run_browser(args, candidates, state))
        print(f"processed={processed}")
        state = load_json(args.state, state)
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
