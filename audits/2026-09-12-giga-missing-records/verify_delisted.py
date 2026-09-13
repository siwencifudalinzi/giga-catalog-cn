#!/usr/bin/env python3
"""Verify current official-directory removals using metadata-only HEAD probes.

The complete official directory crawl in ``official-products.json`` establishes
current absence.  This script then probes a small, deterministic set of legacy
official cover URLs with HEAD requests only.  A surviving official image is
first-party evidence that the title existed; no image body is downloaded.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests


AUDIT_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = AUDIT_DIR / "evidence"
INPUT_CSV = AUDIT_DIR / "missing-records.csv"
OFFICIAL_PRODUCTS = EVIDENCE_DIR / "official-products.json"
PROGRESS_FILE = EVIDENCE_DIR / "delisted-asset-probes.json"
OUTPUT_CSV = AUDIT_DIR / "delisted-verification.csv"
PENDING_CLASS = "历史条目，状态待核实"
CONFIRMED_CLASS = "确认官方下架"


def split_code(code: str) -> Tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z]+)-(\d+)", code.strip())
    if not match:
        raise ValueError(f"unsupported code: {code!r}")
    return match.group(1).upper(), int(match.group(2))


def cover_probe_urls(code: str, preferred_width: Optional[int] = None) -> List[str]:
    prefix, number = split_code(code)
    widths: List[int] = []
    for width in (preferred_width, 0, 2, 3):
        if width is not None and width not in widths:
            widths.append(width)
    urls = []
    for width in widths:
        suffix = str(number) if width == 0 else f"{number:0{width}d}"
        folder = f"{prefix.lower()}{suffix}"
        urls.append(
            f"https://www.giga-web.jp/db_titles/{prefix.lower()}/{folder}/pac_s.jpg"
        )
    return urls


def verification_label(
    *,
    asia_evidence: bool,
    current_directory_absent: bool,
    asset_status: Optional[int],
    content_type: str,
) -> str:
    if (
        asia_evidence
        and current_directory_absent
        and asset_status == 200
        and content_type.lower().startswith("image/")
    ):
        return "确认当前官网目录已移除（官方资源残留）"
    if asset_status is None:
        return "官方资源访问失败，未完成确认"
    return "当前官网目录缺失，只有第三方历史线索"


def infer_widths(products: Iterable[dict]) -> Dict[str, int]:
    counts: Dict[str, Dict[int, int]] = {}
    for product in products:
        code = str(product.get("code", ""))
        cover = str(product.get("cover", ""))
        try:
            prefix, number = split_code(code)
        except ValueError:
            continue
        match = re.search(
            rf"/db_titles/{prefix.lower()}/({prefix.lower()}(\d+))(?:_|/)",
            cover,
            flags=re.IGNORECASE,
        )
        if not match or int(match.group(2)) != number:
            continue
        width = len(match.group(2))
        counts.setdefault(prefix, {})[width] = counts.setdefault(prefix, {}).get(width, 0) + 1
    return {
        prefix: sorted(width_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        for prefix, width_counts in counts.items()
    }


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def probe_url(session: requests.Session, url: str, timeout: float) -> dict:
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        response = session.head(url, allow_redirects=False, timeout=timeout)
        return {
            "url": url,
            "status": response.status_code,
            "contentType": response.headers.get("Content-Type", ""),
            "contentLength": response.headers.get("Content-Length", ""),
            "location": response.headers.get("Location", ""),
            "checkedAt": checked_at,
            "error": "",
        }
    except requests.RequestException as exc:
        return {
            "url": url,
            "status": None,
            "contentType": "",
            "contentLength": "",
            "location": "",
            "checkedAt": checked_at,
            "error": f"{type(exc).__name__}: {exc}",
        }


def select_best_probe(probes: List[dict]) -> dict:
    for probe in probes:
        if probe.get("status") == 200 and str(probe.get("contentType", "")).lower().startswith("image/"):
            return probe
    completed = [probe for probe in probes if probe.get("status") is not None]
    return completed[0] if completed else probes[0]


def write_csv(rows: List[dict]) -> None:
    fields = [
        "规范番号", "系列", "标题", "AsiaMonstr文章日期", "AsiaMonstr详情页",
        "当前官网目录状态", "官方资源HEAD地址", "HTTP状态", "Content-Type",
        "Content-Length", "历史记录位置", "核查时间", "确认结果", "说明",
    ]
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def update_canonical_records(all_rows: List[dict], verification_rows: List[dict]) -> None:
    verified = {row["规范番号"]: row for row in verification_rows}
    fields = list(all_rows[0].keys())
    for row in all_rows:
        result = verified.get(row["规范番号"])
        if not result or not result["确认结果"].startswith("确认"):
            continue
        row["官方证据地址"] = result["官方资源HEAD地址"]
        row["访问结果"] = "not_found_in_complete_directory; official_asset_head_200"
        row["核查时间"] = result["核查时间"]
        row["分类"] = CONFIRMED_CLASS
        row["判断理由"] = (
            "完整官网目录扫描未收录；官网域名下对应历史封面仍返回图像，"
            "确认该作品曾有官方记录且现已从目录移除（未发现下架公告或原因）"
        )
        row["建议处理"] = "有依据标下架"
    with INPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)


def update_report(all_rows: List[dict]) -> None:
    report_path = AUDIT_DIR / "audit-report.md"
    report = report_path.read_text(encoding="utf-8")
    start = report.index("## 核心发现")
    end = report.index("## 数据限制")
    grouped = {
        "确认漏收录": [row for row in all_rows if row["分类"] == "确认漏收录"],
        CONFIRMED_CLASS: [row for row in all_rows if row["分类"] == CONFIRMED_CLASS],
    }
    unresolved = [row for row in all_rows if row["分类"] not in grouped]
    section = [
        "## 核心发现",
        "",
        f"- 当前官方目录明确存在、线上目录未收录：{len(grouped['确认漏收录'])} 条。",
        f"- 当前官方目录已移除且存在官方历史资源残留：{len(grouped[CONFIRMED_CLASS])} 条。",
        f"- 仍需核实：{len(unresolved)} 条，其中包括没有存在证据的空号和仅有第三方线索的条目。",
        "- ‘确认官方下架’在本报告中只表示：完整扫描当前官网目录未收录，同时官网域名下的对应历史封面仍返回真实图像。未发现官方下架公告，也不推测下架时间或原因。",
        "- 431 条可补录记录的发行日期均早于项目默认最早日期 `2007-12-07`，呈现明显的历史日期截断效应。",
        "",
        "## 结论清单",
        "",
        f"- 可补录：{len(grouped['确认漏收录'])}",
        f"- 有依据标下架：{len(grouped[CONFIRMED_CLASS])}",
        f"- 仍需核实：{len(unresolved)}",
        "",
    ]
    labels = [
        ("可补录", grouped["确认漏收录"]),
        ("有依据标下架", grouped[CONFIRMED_CLASS]),
        ("仍需核实", unresolved),
    ]
    for heading, rows in labels:
        section.extend([f"### {heading}", ""])
        for row in rows:
            title = row["标题"] or "标题未知"
            section.append(f"- `{row['规范番号']}` — {row['分类']}：{title}")
        if not rows:
            section.append("- 无")
        section.append("")
    report = report[:start] + "\n".join(section) + "\n" + report[end:]
    report = report.replace(
        "因此 431 条具备当前官方存在证据；412 条没有被自动推断为下架。",
        "因此 431 条具备当前官方存在证据；二次 HEAD-only 核查另确认 288 条已从当前官网目录移除，剩余 124 条仍需核实。",
    )
    report_path.write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.15, help="seconds between HEAD requests")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--restart", action="store_true", help="ignore saved probe progress")
    args = parser.parse_args()

    products = load_json(OFFICIAL_PRODUCTS)
    official_codes = {str(item.get("code", "")).upper() for item in products}
    widths = infer_widths(products)
    with INPUT_CSV.open(encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    candidates = [
        row for row in all_rows
        if row["分类"] == PENDING_CLASS
        or (row["分类"] == CONFIRMED_CLASS and "official_asset_head_200" in row["访问结果"])
    ]

    saved = {} if args.restart or not PROGRESS_FILE.exists() else load_json(PROGRESS_FILE)
    records: Dict[str, dict] = saved.get("records", {})
    session = requests.Session()
    session.headers.update({
        "User-Agent": "GIGA-catalog-metadata-audit/1.0 (+HEAD-only; no media download)",
        "Accept": "*/*",
    })

    for index, row in enumerate(candidates, start=1):
        code = row["规范番号"].upper()
        if code in records:
            continue
        prefix, _ = split_code(code)
        probes: List[dict] = []
        for url in cover_probe_urls(code, widths.get(prefix)):
            probe = probe_url(session, url, args.timeout)
            probes.append(probe)
            if probe["status"] == 200 and probe["contentType"].lower().startswith("image/"):
                break
            time.sleep(max(args.delay, 0))
        records[code] = {
            "code": code,
            "currentDirectoryAbsent": code not in official_codes,
            "asiaEvidence": bool(row["AsiaMonstr详情页地址"]),
            "preferredWidth": widths.get(prefix),
            "probes": probes,
        }
        save_json(PROGRESS_FILE, {
            "method": "HTTP HEAD only; response bodies were not requested",
            "inputClass": PENDING_CLASS,
            "candidateCount": len(candidates),
            "completedCount": len(records),
            "records": records,
        })
        if index % 25 == 0:
            print(f"probed {index}/{len(candidates)}", flush=True)

    output_rows = []
    for row in candidates:
        code = row["规范番号"].upper()
        record = records[code]
        best = select_best_probe(record["probes"])
        label = verification_label(
            asia_evidence=record["asiaEvidence"],
            current_directory_absent=record["currentDirectoryAbsent"],
            asset_status=best.get("status"),
            content_type=best.get("contentType", ""),
        )
        if label.startswith("确认"):
            note = "完整官网目录未收录该番号；官网域名下的历史封面资源仍以图像响应。"
        elif label.startswith("官方资源访问失败"):
            note = "完整官网目录未收录；遗留资源探测均访问失败，不能据此确认下架。"
        else:
            note = "完整官网目录未收录；所测官网历史资源路径未命中，现阶段仅有 AsiaMonstr/项目历史线索。"
        output_rows.append({
            "规范番号": code,
            "系列": row["系列"],
            "标题": row["标题"],
            "AsiaMonstr文章日期": row["文章日期"],
            "AsiaMonstr详情页": row["AsiaMonstr详情页地址"],
            "当前官网目录状态": "完整扫描未收录" if record["currentDirectoryAbsent"] else "已收录",
            "官方资源HEAD地址": best.get("url", ""),
            "HTTP状态": "" if best.get("status") is None else best["status"],
            "Content-Type": best.get("contentType", ""),
            "Content-Length": best.get("contentLength", ""),
            "历史记录位置": row["历史记录位置"],
            "核查时间": best.get("checkedAt", ""),
            "确认结果": label,
            "说明": note,
        })
    write_csv(output_rows)
    update_canonical_records(all_rows, output_rows)
    update_report(all_rows)

    counts: Dict[str, int] = {}
    for row in output_rows:
        counts[row["确认结果"]] = counts.get(row["确认结果"], 0) + 1
    print(json.dumps({"total": len(output_rows), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
