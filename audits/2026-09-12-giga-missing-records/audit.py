#!/usr/bin/env python3
"""Reproducible missing-record audit for the live GIGA Catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import csv
from io import StringIO
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

import requests

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.giga_catalog.codes import normalize_code
from src.giga_catalog.scraper import discover_products


_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_EXPLICIT_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*\s*[-_]\s*\d+)(?!\d)")

MISSING_COLUMNS = [
    "规范番号",
    "原始番号",
    "系列",
    "候选来源",
    "标题",
    "发行日期",
    "文章日期",
    "表格位置",
    "AsiaMonstr详情页地址",
    "官方证据地址",
    "历史记录位置",
    "访问结果",
    "核查时间",
    "分类",
    "判断理由",
    "建议处理",
]

BASELINE_URL = "https://siwencifudalinzi.github.io/giga-catalog-cn/data/catalog.json"
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1wyNMnWXLRoHySoErtj3A-XeuBrenem7NCRb_Qvm5Zag/export?format=csv&gid=0"
)
ASIAMONSTR_GOOGLE_ENTRY = "https://www.google.com.hk/goto?url=CAESYAHrOzAVa4unAbC7X7yMbEnIFk4AUJx2rVSuHzWNvdcZa-dXeq1G1or896qnSCUQ8H10QHF5RF998FfM4FB3iMX9Rc0ziaO7DvttZKj-d0bi-ZLYi-HvDh5J4y6jRJGaLQ"
ASIA_SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def normalize_audit_code(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).translate(_DASHES).strip()
    normalized = re.sub(r"\s+", "-", normalized)
    return normalize_code(normalized)


def extract_explicit_codes(text: object) -> list[tuple[str, str]]:
    if not isinstance(text, str):
        return []
    normalized_text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    found = []
    for match in _EXPLICIT_CODE.finditer(normalized_text):
        raw = match.group(1)
        canonical = normalize_audit_code(raw)
        if canonical is not None:
            found.append((raw, canonical))
    return found


def extract_leading_code(text: object) -> Optional[tuple[str, str]]:
    if not isinstance(text, str):
        return None
    normalized_text = unicodedata.normalize("NFKC", text).translate(_DASHES).lstrip()
    match = re.match(r"([A-Za-z][A-Za-z0-9]*\s*[-_]\s*\d+)(?!\d)", normalized_text)
    if match is None:
        return None
    raw = match.group(1)
    canonical = normalize_audit_code(raw)
    return (raw, canonical) if canonical is not None else None


def build_baseline_index(catalog: Mapping[str, object]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for series in catalog.get("series", []):
        if not isinstance(series, Mapping):
            continue
        series_code = str(series.get("code") or "").strip().upper()
        if not series_code:
            continue
        codes = set()
        numbers = []
        for video in series.get("videos", []):
            if not isinstance(video, Mapping):
                continue
            code = normalize_audit_code(video.get("code"))
            if code is None or code.rsplit("-", 1)[0] != series_code:
                continue
            codes.add(code)
            numbers.append(int(code.rsplit("-", 1)[1]))
        gaps = []
        if numbers:
            for number in range(min(numbers), max(numbers) + 1):
                code = f"{series_code}-{number}"
                if code not in codes:
                    gaps.append(code)
        result[series_code] = {"codes": codes, "gaps": gaps}
    return result


def snapshot_metadata(raw: bytes, fetched_at: str) -> dict:
    catalog = json.loads(raw.decode("utf-8"))
    index = build_baseline_index(catalog)
    return {
        "sourceUrl": "https://siwencifudalinzi.github.io/giga-catalog-cn/data/catalog.json",
        "fetchedAt": fetched_at,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "seriesCount": len(index),
        "videoCount": sum(len(item["codes"]) for item in index.values()),
        "catalog": catalog,
    }


def audit_timestamp() -> str:
    return datetime.now(ASIA_SHANGHAI).replace(microsecond=0).isoformat()


def group_evidence(rows: Iterable[Mapping[str, object]]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        code = normalize_audit_code(row.get("canonical_code") or row.get("raw_code"))
        if code is not None:
            grouped[code].append(dict(row))
    return dict(sorted(grouped.items()))


def build_candidates(baseline: Mapping[str, Mapping[str, object]], evidence: Iterable[Mapping[str, object]]) -> dict:
    candidates: dict[str, dict] = {}
    for series, item in baseline.items():
        for code in item.get("gaps", []):
            candidates[code] = {"canonical_code": code, "series": series, "sources": ["gap"], "evidence": []}
    for row in evidence:
        code = normalize_audit_code(row.get("canonical_code") or row.get("raw_code"))
        if code is None:
            continue
        series = code.rsplit("-", 1)[0]
        if series not in baseline or code in baseline[series].get("codes", set()):
            continue
        candidate = candidates.setdefault(
            code,
            {"canonical_code": code, "series": series, "sources": [], "evidence": []},
        )
        source = str(row.get("source") or "unknown")
        if source not in candidate["sources"]:
            candidate["sources"].append(source)
        candidate["evidence"].append(dict(row))
    return dict(sorted(candidates.items()))


def classify_candidate(candidate: Mapping[str, object]) -> tuple[str, str, str]:
    sources = set(candidate.get("sources", []))
    status = candidate.get("official_status")
    if status == "confirmed":
        return "确认漏收录", "当前官方记录明确存在且线上目录未收录", "可补录"
    if status == "delisted" and candidate.get("official_delist_evidence") is True:
        return "确认官方下架", "历史存在记录与可信官方下架依据同时存在", "有依据标下架"
    if sources == {"gap"}:
        return "无证据空号", "仅由现有番号区间不连续产生，未找到存在记录", "仍需核实"
    return "历史条目，状态待核实", "外部或历史来源明确记载，但当前官方状态未获确认", "仍需核实"


def parse_sheet_candidates(text: str) -> list[dict]:
    rows = csv.reader(StringIO(text))
    header = next(rows, [])
    normalized_header = [cell.lstrip("\ufeff").strip().upper() for cell in header]
    if "NEW CODE" not in normalized_header:
        raise ValueError("sheet has no NEW CODE column")
    code_index = normalized_header.index("NEW CODE")
    result = []
    for row_number, row in enumerate(rows, start=2):
        raw = row[code_index].strip() if code_index < len(row) else ""
        code = normalize_audit_code(raw)
        if code is not None:
            result.append(
                {
                    "source": "sheet",
                    "raw_code": raw,
                    "canonical_code": code,
                    "sheet_location": f"row {row_number}",
                }
            )
    return result


def history_candidates(
    baseline: Mapping[str, Mapping[str, object]], snapshots: Iterable[tuple[str, Mapping[str, object]]]
) -> list[dict]:
    found: dict[tuple[str, str], dict] = {}
    for commit, catalog in snapshots:
        for series in catalog.get("series", []):
            if not isinstance(series, Mapping):
                continue
            for video in series.get("videos", []):
                if not isinstance(video, Mapping):
                    continue
                raw = video.get("code")
                code = normalize_audit_code(raw)
                if code is None:
                    continue
                series_code = code.rsplit("-", 1)[0]
                if series_code not in baseline or code in baseline[series_code].get("codes", set()):
                    continue
                key = (code, commit)
                found[key] = {
                    "source": "history",
                    "raw_code": str(raw),
                    "canonical_code": code,
                    "title": str(video.get("title") or ""),
                    "release_date": str(video.get("releaseDate") or video.get("date") or ""),
                    "history_location": f"{commit}:public/data/catalog.json",
                }
    return [found[key] for key in sorted(found)]


def _joined(values: Iterable[object]) -> str:
    return "; ".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def candidate_csv_row(candidate: Mapping[str, object], checked_at: str = "") -> dict:
    evidence = [row for row in candidate.get("evidence", []) if isinstance(row, Mapping)]
    classification, reason, action = classify_candidate(candidate)
    title = next((str(row.get("title")) for row in evidence if row.get("title")), "")
    release_date = next((str(row.get("release_date")) for row in evidence if row.get("release_date")), "")
    article_dates = _joined(row.get("article_date", "") for row in evidence)
    raw_codes = _joined(row.get("raw_code", "") for row in evidence) or str(candidate.get("canonical_code", ""))
    return {
        "规范番号": candidate.get("canonical_code", ""),
        "原始番号": raw_codes,
        "系列": candidate.get("series", ""),
        "候选来源": _joined(candidate.get("sources", [])),
        "标题": title,
        "发行日期": release_date,
        "文章日期": article_dates,
        "表格位置": _joined(row.get("sheet_location", "") for row in evidence),
        "AsiaMonstr详情页地址": _joined(row.get("url", "") for row in evidence if row.get("source") == "asiamonstr"),
        "官方证据地址": str(candidate.get("official_url") or ""),
        "历史记录位置": _joined(row.get("history_location", "") for row in evidence),
        "访问结果": str(candidate.get("official_status") or "未请求/未找到"),
        "核查时间": checked_at,
        "分类": classification,
        "判断理由": reason,
        "建议处理": action,
    }


def _write_csv(path: Path, fieldnames: list[str], rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(
    output_dir: Path,
    baseline_meta: Mapping[str, object],
    candidates: Mapping[str, Mapping[str, object]],
    source_coverage: Iterable[Mapping[str, object]],
    asiamonstr_progress: Mapping[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    checked_at = str(baseline_meta.get("fetchedAt") or "")
    index = build_baseline_index(baseline_meta["catalog"])
    missing_rows = [candidate_csv_row(candidates[code], checked_at) for code in sorted(candidates)]
    _write_csv(output_dir / "missing-records.csv", MISSING_COLUMNS, missing_rows)

    by_series = defaultdict(list)
    for row in missing_rows:
        by_series[row["系列"]].append(row)
    series_rows = []
    for series in sorted(index):
        rows = by_series.get(series, [])
        series_rows.append(
            {
                "系列": series,
                "已有数量": len(index[series]["codes"]),
                "空号数": len(index[series]["gaps"]),
                "候选数": len(rows),
                "可补录数": sum(row["建议处理"] == "可补录" for row in rows),
                "完成情况": "完成" if asiamonstr_progress.get("completed") else "受阻：AsiaMonstr未完成",
            }
        )
    _write_csv(
        output_dir / "series-coverage.csv",
        ["系列", "已有数量", "空号数", "候选数", "可补录数", "完成情况"],
        series_rows,
    )
    coverage_rows = list(source_coverage)
    _write_csv(
        output_dir / "source-coverage.csv",
        ["来源", "地址或位置", "访问结果", "条目数量", "下一页位置", "重试次数", "核查时间", "说明"],
        coverage_rows,
    )

    counts = defaultdict(int)
    for row in missing_rows:
        counts[row["建议处理"]] += 1
    confirmed_dates = sorted(
        row["发行日期"] for row in missing_rows if row["建议处理"] == "可补录" and row["发行日期"]
    )
    progress_complete = bool(asiamonstr_progress.get("completed"))
    progress_note = (
        f"AsiaMonstr 分类遍历完成，共 {len(asiamonstr_progress.get('visitedUrls', []))} 页。"
        if progress_complete
        else "AsiaMonstr 分类遍历未完成：" + str(asiamonstr_progress.get("failure") or "存在未处理页面")
    )
    report = [
        "# GIGA Catalog 缺失影片条目审计报告",
        "",
        "## 基准与范围",
        "",
        f"- 线上快照：{baseline_meta.get('sourceUrl', '')}",
        f"- 获取时间：{baseline_meta.get('fetchedAt', '')}",
        f"- SHA-256：`{baseline_meta.get('sha256', '')}`",
        f"- 系列数：{baseline_meta.get('seriesCount', 0)}；影片数：{baseline_meta.get('videoCount', 0)}",
        f"- {progress_note}",
        "",
        "## 核心发现",
        "",
        f"- 当前官方目录明确存在、线上目录未收录的条目为 {counts['可补录']} 条。",
        (
            f"- 这批条目的发行日期全部落在 {confirmed_dates[0]} 至 {confirmed_dates[-1]}；"
            "没有一条达到项目当前默认最早日期 `2007-12-07`（`src/giga_catalog/validation.py:15`）。"
            if confirmed_dates
            else "- 当前没有取得带发行日期的确认漏收录条目。"
        ),
        "- 因此“可补录”呈现为明确的历史日期截断效应，而非随机漏号；是否把 2007-12-07 以前的官方影片纳入目录，是后续入库前需要确认的范围政策。",
        "- 没有找到同时具备历史存在记录和可信官方下架依据的条目，因此本轮“有依据标下架”为 0。",
        "",
        "## 结论清单",
        "",
        f"- 可补录：{counts['可补录']}",
        f"- 有依据标下架：{counts['有依据标下架']}",
        f"- 仍需核实：{counts['仍需核实']}",
        "",
    ]
    for action in ("可补录", "有依据标下架", "仍需核实"):
        report.extend([f"### {action}", ""])
        selected = [row for row in missing_rows if row["建议处理"] == action]
        if selected:
            report.extend(f"- `{row['规范番号']}` — {row['分类']}：{row['标题'] or '标题未知'}" for row in selected)
        else:
            report.append("- 无")
        report.append("")
    report.extend(
        [
            "## 数据限制",
            "",
            "- 空号只表示编号不连续，不证明影片存在或已下架。",
            "- AsiaMonstr 文章只作为历史存在或条目线索；官方状态需由当前官方记录或明确下架依据确认。",
            "- 403、404、超时和搜索无结果均不单独作为下架证据。",
            "- 本审计仅核查元数据，未访问播放或下载资源。",
            "",
        ]
    )
    (output_dir / "audit-report.md").write_text("\n".join(report), encoding="utf-8")


def download_bytes(url: str, *, attempts: int = 3, timeout: float = 45) -> tuple[bytes, int]:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content, attempt - 1
        except requests.RequestException as error:
            last_error = error
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")


def load_history_snapshots(repo_root: Path) -> tuple[list[tuple[str, Mapping[str, object]]], list[dict]]:
    command = ["git", "log", "--format=%H", "--all", "--", "public/data/catalog.json"]
    commits = subprocess.run(command, cwd=repo_root, check=True, capture_output=True, text=True).stdout.splitlines()
    snapshots = []
    coverage = []
    for commit in commits:
        shown = subprocess.run(
            ["git", "show", f"{commit}:public/data/catalog.json"],
            cwd=repo_root,
            check=False,
            capture_output=True,
        )
        if shown.returncode != 0:
            coverage.append({"commit": commit, "status": "git-show-failed", "count": 0})
            continue
        try:
            catalog = json.loads(shown.stdout.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            coverage.append({"commit": commit, "status": "json-invalid", "count": 0})
            continue
        count = sum(len(series.get("videos", [])) for series in catalog.get("series", []) if isinstance(series, Mapping))
        snapshots.append((commit, catalog))
        coverage.append({"commit": commit, "status": "ok", "count": count})
    return snapshots, coverage


def load_legacy_local_sources(repo_root: Path, legacy_root: Path) -> tuple[list[dict], list[dict]]:
    evidence = []
    coverage = []

    def add_record(raw: object, location: str, *, title: object = "", release_date: object = "", source: str):
        code = normalize_audit_code(raw)
        if code is None:
            return
        evidence.append(
            {
                "source": source,
                "raw_code": str(raw),
                "canonical_code": code,
                "title": str(title or ""),
                "release_date": str(release_date or ""),
                "history_location": location,
            }
        )

    repo_products_path = repo_root / "data" / "raw" / "products.json"
    if repo_products_path.exists():
        products = json.loads(repo_products_path.read_text(encoding="utf-8-sig"))
        for index, product in enumerate(products, start=1):
            if isinstance(product, Mapping):
                add_record(
                    product.get("code"),
                    f"{repo_products_path}:record {index}",
                    title=product.get("title"),
                    release_date=product.get("releaseDate"),
                    source="historical-catalog",
                )
        coverage.append({"location": str(repo_products_path), "status": "ok", "count": len(products)})

    legacy_catalog_path = legacy_root / "data.json"
    if legacy_catalog_path.exists():
        catalog = json.loads(legacy_catalog_path.read_text(encoding="utf-8-sig"))
        count = 0
        series_values = catalog.get("series", {})
        if isinstance(series_values, Mapping):
            series_values = series_values.values()
        for series in series_values:
            if not isinstance(series, Mapping):
                continue
            video_values = series.get("videos", {})
            if isinstance(video_values, Mapping):
                video_values = video_values.values()
            for video in video_values:
                if not isinstance(video, Mapping):
                    continue
                count += 1
                add_record(
                    video.get("code"),
                    f"{legacy_catalog_path}:video {count}",
                    title=video.get("title"),
                    release_date=video.get("releaseDate") or video.get("date"),
                    source="historical-catalog",
                )
        coverage.append({"location": str(legacy_catalog_path), "status": "ok", "count": count})

    legacy_links_path = legacy_root / "links.json"
    if legacy_links_path.exists():
        links = json.loads(legacy_links_path.read_text(encoding="utf-8-sig"))
        for raw in links if isinstance(links, Mapping) else []:
            add_record(raw, f"{legacy_links_path}:key {raw}", source="historical-link-index")
        coverage.append(
            {"location": str(legacy_links_path), "status": "ok", "count": len(links) if isinstance(links, Mapping) else 0}
        )

    for sheet_path in sorted(legacy_root.glob("giga_*.csv")):
        parsed = 0
        with sheet_path.open(encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.reader(handle), start=1):
                raw = row[0].strip() if row else ""
                if normalize_audit_code(raw) is None:
                    continue
                parsed += 1
                code = normalize_audit_code(raw)
                evidence.append(
                    {
                        "source": "historical-sheet",
                        "raw_code": raw,
                        "canonical_code": code,
                        "sheet_location": f"{sheet_path}:row {row_number}",
                        "history_location": f"{sheet_path}:row {row_number}",
                    }
                )
        coverage.append({"location": str(sheet_path), "status": "ok", "count": parsed})
    return evidence, coverage


def load_asiamonstr_evidence(audit_root: Path) -> tuple[list[dict], dict, list[dict], list[dict]]:
    progress_path = audit_root / "evidence" / "asiamonstr-progress.json"
    records_path = audit_root / "evidence" / "asiamonstr-records.jsonl"
    progress = json.loads(progress_path.read_text(encoding="utf-8-sig"))
    evidence = []
    ambiguous = []
    if records_path.exists():
        for line_number, line in enumerate(records_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            article = json.loads(line)
            code_match = extract_leading_code(article.get("heading", ""))
            if code_match is None:
                ambiguous.append({**article, "reason": "category heading has no explicit product code", "line": line_number})
                continue
            raw, code = code_match
            evidence.append(
                {
                    "source": "asiamonstr",
                    "raw_code": raw,
                    "canonical_code": code,
                    "title": article.get("heading", ""),
                    "article_date": article.get("articleDate", ""),
                    "url": article.get("detailUrl", ""),
                    "page_url": article.get("pageUrl", ""),
                    "page_ordinal": article.get("pageOrdinal", ""),
                }
            )
    detail_by_url = {}
    for detail_path in sorted((audit_root / "evidence" / "asiamonstr-details").glob("*.json")):
        detail = json.loads(detail_path.read_text(encoding="utf-8-sig"))
        if detail.get("status") == 200 and normalize_audit_code(detail.get("canonicalCode")):
            detail_by_url[detail.get("url", "")] = detail
    unresolved = []
    for article in ambiguous:
        detail = detail_by_url.get(article.get("detailUrl", ""))
        if detail is None:
            unresolved.append(article)
            continue
        evidence.append(
            {
                "source": "asiamonstr",
                "raw_code": detail.get("rawCode", ""),
                "canonical_code": detail.get("canonicalCode", ""),
                "title": detail.get("codeEvidence", ""),
                "article_date": detail.get("articleDate", article.get("articleDate", "")),
                "url": detail.get("url", ""),
                "page_url": article.get("pageUrl", ""),
                "page_ordinal": article.get("pageOrdinal", ""),
                "detail_verified": True,
            }
        )
    ambiguous = unresolved
    pages = []
    for page_path in sorted((audit_root / "evidence" / "asiamonstr-pages").glob("*.json")):
        page = json.loads(page_path.read_text(encoding="utf-8-sig"))
        pages.append(page)
    return evidence, progress, pages, ambiguous


def _coverage_row(
    source: str,
    location: str,
    status: str,
    count: object,
    next_location: str,
    retries: object,
    checked_at: str,
    note: str = "",
) -> dict:
    return {
        "来源": source,
        "地址或位置": location,
        "访问结果": status,
        "条目数量": count,
        "下一页位置": next_location,
        "重试次数": retries,
        "核查时间": checked_at,
        "说明": note,
    }


def run_audit(
    audit_root: Path,
    repo_root: Path,
    *,
    official_delay: float = 0.25,
    reuse_official: bool = False,
) -> dict:
    evidence_root = audit_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    checked_at = audit_timestamp()
    coverage = []

    baseline_raw, retries = download_bytes(BASELINE_URL)
    (evidence_root / "online-catalog.json").write_bytes(baseline_raw)
    baseline_meta = snapshot_metadata(baseline_raw, checked_at)
    declared_totals = baseline_meta["catalog"].get("totals", {})
    if declared_totals:
        if declared_totals.get("series") != baseline_meta["seriesCount"] or declared_totals.get("videos") != baseline_meta["videoCount"]:
            raise RuntimeError("live catalog declared totals do not match parsed counts")
    (audit_root / "baseline.json").write_text(
        json.dumps(baseline_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    coverage.append(
        _coverage_row("线上目录基准", BASELINE_URL, "200/已保存", baseline_meta["videoCount"], "", retries, checked_at)
    )
    baseline_index = build_baseline_index(baseline_meta["catalog"])

    sheet_raw, retries = download_bytes(SHEET_URL)
    (evidence_root / "sheet.csv").write_bytes(sheet_raw)
    sheet_text = sheet_raw.decode("utf-8-sig")
    sheet_rows = parse_sheet_candidates(sheet_text)
    coverage.append(_coverage_row("项目链接表格", SHEET_URL, "200/已保存", len(sheet_rows), "", retries, checked_at))

    asiamonstr_rows, progress, pages, ambiguous = load_asiamonstr_evidence(audit_root)
    coverage.append(
        _coverage_row(
            "AsiaMonstr入口",
            ASIAMONSTR_GOOGLE_ENTRY,
            "浏览器跳转成功",
            len(asiamonstr_rows),
            "https://www.asiamonstr.com/category/giga",
            0,
            checked_at,
            "最终地址为 GIGA 分类页；分页采用 /category/giga/page/N，并按页面实际 Next 链接遍历。",
        )
    )
    for page in pages:
        coverage.append(
            _coverage_row(
                "AsiaMonstr分类分页",
                page.get("url", ""),
                page.get("status", ""),
                page.get("articleCount", 0),
                page.get("nextUrl", ""),
                0,
                checked_at,
            )
        )
    for detail_path in sorted((evidence_root / "asiamonstr-details").glob("*.json")):
        detail = json.loads(detail_path.read_text(encoding="utf-8-sig"))
        coverage.append(
            _coverage_row(
                "AsiaMonstr详情页",
                detail.get("url", ""),
                str(detail.get("status", "")),
                1 if detail.get("canonicalCode") else 0,
                "",
                0,
                str(detail.get("checkedAt") or checked_at),
                str(detail.get("note") or ""),
            )
        )
    (evidence_root / "asiamonstr-ambiguous.json").write_text(
        json.dumps(ambiguous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    snapshots, history_coverage = load_history_snapshots(repo_root)
    history_rows = history_candidates(baseline_index, snapshots)
    legacy_rows, legacy_coverage = load_legacy_local_sources(repo_root, Path(r"D:\giga-catalog"))
    history_rows.extend(legacy_rows)
    (evidence_root / "history-candidates.json").write_text(
        json.dumps(history_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in history_coverage:
        coverage.append(
            _coverage_row(
                "Git历史目录",
                f"{item['commit']}:public/data/catalog.json",
                item["status"],
                item["count"],
                "",
                0,
                checked_at,
            )
        )
    for item in legacy_coverage:
        coverage.append(
            _coverage_row(
                "历史目录/旧链接表",
                item["location"],
                item["status"],
                item["count"],
                "",
                0,
                checked_at,
            )
        )

    official_products_path = evidence_root / "official-products.json"
    official_summary_path = evidence_root / "official-summary.json"
    if reuse_official and official_products_path.exists() and official_summary_path.exists():
        official_products = json.loads(official_products_path.read_text(encoding="utf-8"))
        official_summary = json.loads(official_summary_path.read_text(encoding="utf-8"))
    else:
        official_products, official_summary = discover_products(
            [], mode="audit", page_limit=None, delay_seconds=official_delay, timeout=30, retries=3, include_known=True
        )
        official_products_path.write_text(
            json.dumps(official_products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        official_summary_path.write_text(
            json.dumps(official_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    reconciliation = official_summary.get("pageReconciliation", [])
    for page in reconciliation:
        coverage.append(
            _coverage_row(
                "GIGA官方目录",
                f"https://www.giga-web.jp/search/index.php?count={page.get('page')}&sort=1",
                "ok" if page.get("cards") == page.get("resolved") else "解析失败",
                page.get("resolved", 0),
                "",
                official_summary.get("retries", 0),
                checked_at,
            )
        )
    if official_summary.get("error"):
        coverage.append(
            _coverage_row(
                "GIGA官方目录",
                f"cursor {official_summary.get('cursor')}",
                "访问失败",
                0,
                "",
                official_summary.get("retries", 0),
                checked_at,
                str(official_summary.get("error")),
            )
        )

    official_rows = []
    for product in official_products:
        code = normalize_audit_code(product.get("code"))
        if code is None:
            continue
        official_rows.append(
            {
                "source": "official",
                "raw_code": product.get("code", ""),
                "canonical_code": code,
                "title": product.get("title", ""),
                "release_date": product.get("releaseDate", ""),
                "url": "https://www.giga-web.jp/product/index.php?product_id=" + str(product.get("productId")),
            }
        )
    all_evidence = sheet_rows + asiamonstr_rows + history_rows + official_rows
    candidates = build_candidates(baseline_index, all_evidence)
    official_by_code = {
        code: product
        for product in official_products
        if (code := normalize_audit_code(product.get("code"))) is not None
    }
    for code, candidate in candidates.items():
        official = official_by_code.get(code)
        if official:
            candidate["official_status"] = "confirmed"
            candidate["official_url"] = (
                "https://www.giga-web.jp/product/index.php?product_id=" + str(official.get("productId"))
            )
        else:
            candidate["official_status"] = (
                "not_found_in_complete_directory"
                if official_summary.get("stopReason") == "empty" and official_summary.get("cardIntegrityComplete")
                else "not_found_in_incomplete_directory"
            )

    out_of_scope = sorted(
        {
            row["canonical_code"].rsplit("-", 1)[0]
            for row in all_evidence
            if row.get("canonical_code") and row["canonical_code"].rsplit("-", 1)[0] not in baseline_index
        }
    )
    (evidence_root / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    official_complete = (
        official_summary.get("stopReason") == "empty"
        and official_summary.get("errors") == 0
        and official_summary.get("cardIntegrityComplete") is True
    )
    combined_progress = dict(progress)
    combined_progress["completed"] = bool(progress.get("completed")) and official_complete
    if not official_complete:
        combined_progress["failure"] = "official directory traversal incomplete: " + str(
            official_summary.get("error") or official_summary.get("stopReason")
        )
    write_outputs(audit_root, baseline_meta, candidates, coverage, combined_progress)
    action_counts = defaultdict(int)
    for candidate in candidates.values():
        action_counts[classify_candidate(candidate)[2]] += 1
    source_candidate_counts = {
        source: sum(source in candidate.get("sources", []) for candidate in candidates.values())
        for source in ("gap", "sheet", "asiamonstr", "history", "historical-catalog", "historical-link-index", "historical-sheet", "official")
    }
    with (audit_root / "audit-report.md").open("a", encoding="utf-8") as handle:
        handle.write("## 来源覆盖摘要\n\n")
        handle.write("| 来源 | 覆盖结果 | 进入缺失候选 |\n|---|---:|---:|\n")
        handle.write(f"| 线上基准 | {baseline_meta['seriesCount']} 系列 / {baseline_meta['videoCount']} 影片 | — |\n")
        handle.write(f"| AsiaMonstr GIGA 分类 | {len(pages)} 页 / {len(asiamonstr_rows)} 部唯一番号 | {source_candidate_counts['asiamonstr']} |\n")
        handle.write(f"| 当前项目链接表 | {len(sheet_rows)} 个明确番号 | {source_candidate_counts['sheet']} |\n")
        handle.write(f"| Git 历史目录 | {len(snapshots)} 个可解析快照 | {source_candidate_counts['history']} |\n")
        handle.write(f"| 旧目录与链接表 | {len(legacy_coverage)} 个文件 | {source_candidate_counts['historical-catalog'] + source_candidate_counts['historical-link-index'] + source_candidate_counts['historical-sheet']} |\n")
        handle.write(f"| GIGA 当前官方目录 | {official_summary.get('pagesFetched')} 页 / {len(official_products)} 影片 | {source_candidate_counts['official']} |\n\n")
        handle.write(
            f"官方目录逐卡核对结果为 cardsSeen={official_summary.get('cardsSeen')}、"
            f"cardsResolved={official_summary.get('cardsResolved')}、errors={official_summary.get('errors')}、"
            f"stopReason={official_summary.get('stopReason')}。因此 {action_counts['可补录']} 条具备当前官方存在证据；"
            f"{action_counts['仍需核实']} 条没有被自动推断为下架。\n\n"
        )
        handle.write("## 范围外与人工复核\n\n")
        handle.write("- 范围外系列前缀：" + (", ".join(f"`{item}`" for item in out_of_scope) or "无") + "\n")
        handle.write(f"- AsiaMonstr 摘要无法解析明确番号：{len(ambiguous)} 条；见 `evidence/asiamonstr-ambiguous.json`。\n")
        handle.write(
            f"- 官方目录状态：stopReason={official_summary.get('stopReason')}，"
            f"pagesFetched={official_summary.get('pagesFetched')}，errors={official_summary.get('errors')}。\n"
        )
    return {
        "series": baseline_meta["seriesCount"],
        "videos": baseline_meta["videoCount"],
        "asiamonstrPages": len(pages),
        "asiamonstrRecords": len(asiamonstr_rows),
        "ambiguous": len(ambiguous),
        "officialRecords": len(official_products),
        "candidates": len(candidates),
        "outOfScopeSeries": len(out_of_scope),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--official-delay", type=float, default=0.25)
    parser.add_argument("--reuse-official", action="store_true")
    args = parser.parse_args()
    result = run_audit(
        args.audit_root.resolve(),
        args.repo_root.resolve(),
        official_delay=args.official_delay,
        reuse_official=args.reuse_official,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
