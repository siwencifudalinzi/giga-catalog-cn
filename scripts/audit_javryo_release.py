"""Check the JAVRyo release boundary and write a public aggregate audit."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo import build_manifest_entries  # noqa: E402
from src.giga_catalog.resolved_links import load_json  # noqa: E402

FORBIDDEN = re.compile(rb"\.m3u8|blob:|cookie|token|[?&](?:expires|signature|sig|x-amz-)", re.I)
STATUSES = ("verified", "media_reachable", "blocked", "retryable", "dead", "unsupported")


def _videos(catalog: dict) -> dict:
    return {video["code"]: video for series in catalog["series"] for video in series["videos"]}


def audit(root: Path, baseline_path: Path) -> dict:
    before = load_json(baseline_path, {})
    catalog = load_json(root / "public/data/catalog.json", {})
    overlay = load_json(root / "data/javryo-links.json", {})
    candidates = load_json(root / "data/javryo-embeds.json", {})
    embed_state = load_json(root / "data/state/javryo-embed-verification.json", {})
    tape_state = load_json(root / "data/state/javryo-streamtape-verification.json", {})
    manifest = load_json(root / "public/data/resolved-links.json", {})
    bootstrap = load_json(root / "public/data/catalog-bootstrap.json", {})
    old_videos, new_videos = _videos(before), _videos(catalog)
    assert set(old_videos) == set(new_videos), "catalog film codes changed"
    assert before["totals"]["videos"] == catalog["totals"]["videos"] == len(new_videos)
    for code in old_videos:
        old_links = {k: v for k, v in old_videos[code].get("links", {}).items() if k != "javryo"}
        new_links = {k: v for k, v in new_videos[code].get("links", {}).items() if k != "javryo"}
        assert old_links == new_links, f"existing links changed for {code}"

    overlays = overlay["entries"]
    inventory = candidates["entries"]
    embed_rows = embed_state["results"]
    tape_rows = tape_state["results"]
    assert len(overlays) == len(inventory) == len(embed_rows) == 1923
    assert set(overlays) == set(inventory) == set(embed_rows)
    assert len(tape_rows) == 232
    for code, item in overlays.items():
        assert new_videos[code]["links"]["javryo"] == item["pageUrl"], code

    expected = build_manifest_entries(overlay, embed_state, tape_state, candidates)
    actual = {code: {"standard.javryo": slots["standard.javryo"]}
              for code, slots in manifest["entries"].items() if "standard.javryo" in slots}
    assert actual == expected, "published JAVRyo entries differ from verified state"
    providers = Counter()
    published_hosts = Counter()
    for code, slots in actual.items():
        entry = slots["standard.javryo"]
        provider = entry["provider"]
        providers[provider] += 1
        row = (embed_rows if provider == "javryo_stream" else tape_rows)[code]
        paths = row["paths"]
        direct = paths["direct"]["evidence"]
        assert paths["direct"]["status"] == "verified"
        assert any(event in ("playing", "timeupdate") for event in direct.get("events", [])), code
        assert direct.get("manifestStatus") == 200 or direct.get("mediaStatus") in (200, 206), code
        if provider == "javryo_stream":
            source = paths["source"]["evidence"]
            assert paths["source"]["status"] == "reached"
            assert source.get("embedFrameSeen") and source.get("embedStatus") == 200, code
            published_hosts[inventory[code]["host"]] += 1
        else:
            assert paths["source"]["status"] == "verified", code
            assert any(event in ("playing", "timeupdate")
                       for event in paths["source"]["evidence"].get("events", [])), code

    for path in (root / "public").rglob("*.json"):
        assert not FORBIDDEN.search(path.read_bytes()), f"temporary or credential text in {path}"
    for path in (root / "data/state/javryo-embed-verification.json",
                 root / "data/state/javryo-streamtape-verification.json"):
        assert not FORBIDDEN.search(path.read_bytes()), f"private state contains media URL or credential: {path}"

    embed_counts = Counter(row.get("playbackStatus") for row in embed_rows.values())
    tape_counts = Counter(row.get("playbackStatus") for row in tape_rows.values())
    assert set(embed_counts).issubset(STATUSES) and set(tape_counts).issubset(STATUSES)
    summary = {
        "generation": bootstrap["generation"],
        "films": len(new_videos),
        "javryoPages": len(overlays),
        "embedVerification": {status: embed_counts[status] for status in STATUSES},
        "streamtapeVerification": {status: tape_counts[status] for status in STATUSES},
        "published": {"total": len(actual), "byProvider": dict(sorted(providers.items())),
                      "embedHosts": dict(sorted(published_hosts.items()))},
        "googleSheetAndOtherExistingLinksPreserved": True,
        "publicJsonContainsTemporaryMediaOrCredentials": False,
        "everyPublishedEntryHasBothPathEvidence": True,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "audits/javryo-embedded-2026-09-29/summary.json")
    args = parser.parse_args()
    summary = audit(args.root, args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
