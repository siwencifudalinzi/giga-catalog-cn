"""Prepare, compare, and verify a GIGA production release."""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.giga_catalog.release import (  # noqa: E402
    MANIFEST_RELATIVE_PATH,
    PRODUCTION_URL,
    SITE_ID,
    ReleaseError,
    compare_live_release,
    parse_manifest,
    validate_deploy_json,
    validate_local_release,
    verify_live_release,
    write_manifest,
)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    _add_local_paths(prepare)
    prepare.add_argument("--source-commit", required=True)

    precheck = subparsers.add_parser("precheck")
    _add_local_paths(precheck)
    _add_network_options(precheck, attempts=3, timeout=10.0, delay=2.0)
    precheck.add_argument("--github-output", type=Path)

    deploy = subparsers.add_parser("validate-deploy")
    deploy.add_argument("deploy_json", type=Path)
    deploy.add_argument("--site-id", default=SITE_ID)
    deploy.add_argument("--url", default=PRODUCTION_URL)

    for command in ("verify", "verify-production"):
        verify = subparsers.add_parser(command)
        _add_local_paths(verify)
        _add_network_options(verify, attempts=6, timeout=30.0, delay=10.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    options = create_parser().parse_args(argv)
    try:
        if options.command == "prepare":
            manifest = write_manifest(
                options.public_dir,
                options.source_commit,
                options.netlify_config,
            )
            print(_compact_json(manifest))
            return 0

        if options.command == "validate-deploy":
            payload = validate_deploy_json(
                options.deploy_json.read_bytes(),
                options.site_id,
                options.url,
            )
            print(
                f"Validated production deploy {payload['deploy_id']} "
                f"for site {payload['site_id']}."
            )
            return 0

        manifest = _load_local_manifest(
            options.public_dir,
            options.netlify_config,
        )
        if options.command == "precheck":
            result = compare_live_release(
                manifest,
                options.url,
                attempts=options.attempts,
                timeout=options.timeout,
                delay=options.delay,
            )
            deploy_required = result.state != "matching"
            if options.github_output is not None:
                with options.github_output.open("a", encoding="utf-8", newline="\n") as output:
                    output.write(
                        f"deploy_required={str(deploy_required).lower()}\n"
                    )
                    output.write(f"production_state={result.state}\n")
            print(
                f"Production pre-check: {result.state} after "
                f"{result.attempts} attempt(s): {result.detail}"
            )
            return 0

        if options.command in {"verify", "verify-production"}:
            result = verify_live_release(
                manifest,
                options.url,
                attempts=options.attempts,
                timeout=options.timeout,
                delay=options.delay,
            )
            print(
                f"Production verification: {result.state} after "
                f"{result.attempts} attempt(s): {result.detail}"
            )
            return 0 if result.state == "matching" else 1
    except (ReleaseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    raise AssertionError("unreachable command")


def _add_local_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--public-dir",
        type=Path,
        default=REPOSITORY_ROOT / "public",
    )
    parser.add_argument(
        "--netlify-config",
        type=Path,
        default=REPOSITORY_ROOT / "netlify.toml",
    )


def _add_network_options(
    parser: argparse.ArgumentParser,
    *,
    attempts: int,
    timeout: float,
    delay: float,
) -> None:
    parser.add_argument("--url", default=PRODUCTION_URL)
    parser.add_argument("--attempts", type=int, default=attempts)
    parser.add_argument("--timeout", type=float, default=timeout)
    parser.add_argument("--delay", type=float, default=delay)


def _load_local_manifest(public_dir: Path, netlify_config: Path) -> dict:
    manifest_path = public_dir / MANIFEST_RELATIVE_PATH
    manifest = parse_manifest(manifest_path.read_bytes())
    return validate_local_release(manifest, public_dir, netlify_config)


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
