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
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION, classify_observation, classify_source_observation  # noqa: E402
from src.giga_catalog.resolved_links import load_json, source_url_hash  # noqa: E402

FORBIDDEN = re.compile(rb"\.m3u8|blob:|cookie|token|[?&](?:expires|signature|sig|x-amz-)", re.I)
STATUSES = ("verified", "media_reachable", "blocked", "retryable", "dead", "unsupported")


def _videos(catalog: dict) -> dict:
    return {video["code"]: video for series in catalog["series"] for video in series["videos"]}


def assert_preview_entry(code: str, entry: dict, preview: dict, generation: str) -> None:
    assert (preview.get("generation") == generation
            and preview.get("targetUrlHash") == source_url_hash(entry["finalUrl"])
            and preview.get("verificationVersion") == VERIFICATION_VERSION
            and preview.get("playbackStatus") == "verified"), f"fresh GIGA preview proof missing: {code}"
    for path_name in ("catalog", "direct"):
        proof = preview.get("paths", {}).get(path_name, {})
        assert (proof.get("status") == "verified"
                and classify_observation(proof.get("evidence", {})) == "verified"), code


def audit(root: Path, baseline_path: Path) -> dict:
    before = load_json(baseline_path, {})
    catalog = load_json(root / "public/data/catalog.json", {})
    overlay = load_json(root / "data/javryo-links.json", {})
    candidates = load_json(root / "data/javryo-embeds.json", {})
    embed_state = load_json(root / "data/state/javryo-embed-verification.json", {})
    tape_state = load_json(root / "data/state/javryo-streamtape-verification.json", {})
    manifest = load_json(root / "public/data/resolved-links.json", {})
    bootstrap = load_json(root / "public/data/catalog-bootstrap.json", {})
    preview_rows = load_json(root / "data/state/javryo-preview-verification.json", {}).get("results", {})
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
    assert all(row.get("verificationVersion") == VERIFICATION_VERSION
               for row in [*embed_rows.values(), *tape_rows.values()]), "obsolete verification evidence"
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
        preview = preview_rows.get(code, {})
        assert_preview_entry(code, entry, preview, bootstrap["generation"])
        provider = entry["provider"]
        providers[provider] += 1
        row = (embed_rows if provider == "javryo_stream" else tape_rows)[code]
        paths = row["paths"]
        direct = paths["direct"]["evidence"]
        assert paths["direct"]["status"] == "verified"
        assert classify_observation(direct) == "verified", code
        assert any(event in ("playing", "timeupdate") for event in direct.get("events", [])), code
        assert direct.get("manifestStatus") == 200 or direct.get("mediaStatus") in (200, 206), code
        if provider == "javryo_stream":
            source = paths["source"]["evidence"]
            assert paths["source"]["status"] == "reached"
            assert source.get("embedFrameSeen") and source.get("embedStatus") == 200, code
            assert classify_source_observation(source) == "reached", code
            published_hosts[inventory[code]["host"]] += 1
        else:
            assert paths["source"]["status"] == "verified", code
            assert classify_observation(paths["source"]["evidence"]) == "verified", code
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
        "streamtapeEvidence": {
            "directPageHttp200": sum(row.get("paths", {}).get("direct", {}).get("evidence", {}).get("httpStatus") == 200
                                     for row in tape_rows.values()),
            "directManifestSuccess": sum(row.get("paths", {}).get("direct", {}).get("evidence", {}).get("manifestStatus") == 200
                                         for row in tape_rows.values()),
            "directTrustedPlayback": sum(classify_observation(row.get("paths", {}).get("direct", {}).get("evidence", {})) == "verified"
                                         for row in tape_rows.values()),
        },
        "published": {"total": len(actual), "byProvider": dict(sorted(providers.items())),
                      "embedHosts": dict(sorted(published_hosts.items()))},
        "googleSheetAndOtherExistingLinksPreserved": True,
        "publicJsonContainsTemporaryMediaOrCredentials": False,
        "everyPublishedEntryHasBothPathEvidence": True,
        "everyPublishedEntryPassedGigaButtonAndDirectPreview": True,
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
    counts = summary["embedVerification"]
    tape = summary["streamtapeVerification"]
    report = ["# JAVRyo 稳定播放器直达发布审计", "",
              f"Generation：`{summary['generation']}`", "",
              f"影片总数：{summary['films']}；JAVRyo 详情页：{summary['javryoPages']}。", "",
              f"- 确认可播放、允许发布：{summary['published']['total']}",
              f"- 内置播放器两路径确认可播放：{counts['verified']}",
              f"- 仅播放器与媒体清单可达：{counts['media_reachable']}",
              f"- 人机验证或网络受阻：{counts['blocked'] + counts['retryable']}",
              f"- 已失效：{counts['dead']}", f"- 不支持：{counts['unsupported']}", "",
              "## Streamtape 重新验证", "",
              f"- 两路径确认可播放：{tape['verified']}",
              f"- 仅播放器与媒体清单可达：{tape['media_reachable']}",
              f"- 受阻或待重试：{tape['blocked'] + tape['retryable']}",
              f"- 已失效：{tape['dead']}；不支持：{tape['unsupported']}",
              f"- 直接打开页面 HTTP 200：{summary['streamtapeEvidence']['directPageHttp200']}",
              f"- 直接打开媒体清单成功：{summary['streamtapeEvidence']['directManifestSuccess']}",
              f"- 直接打开出现可信播放事件：{summary['streamtapeEvidence']['directTrustedPlayback']}", "",
              "## 发布检查", "",
              "影片总数及原 Google 表格和其他来源链接保持不变。公开 JSON 未发现临时媒体地址或凭据。",
              "所有允许发布的条目都有来源点击、直接打开，以及 GIGA 站内按钮预览的真实播放证据。",
              "私有逐条验证记录保存于本地和 Documents 持久备份，不提交 GitHub。", "",
              "GitHub main 提交和 GitHub Pages 部署结果以最终发布报告及 Actions 记录为准。", ""]
    args.output.with_name("report.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
