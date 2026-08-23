# GIGA 全量直达链接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将公开目录中的全部 3122 条 ouo 链接放入可恢复队列，采集并发布每条可验证的 Gofile、Streamtape 或 Player4me 最终公开落地页，同时保持原链回退。

**Architecture:** Python 核心模块负责目录遍历、稳定 slot、URL 安全校验、状态恢复和确定性 manifest；本机 Playwright 持久 Chrome 只执行站点正常按钮流程并关闭广告弹窗。前端改用 schema v2 的 `code + slot + source hash` 命中，支持普通版和无码版同提供商并存。

**Tech Stack:** Python 3.9+、Playwright Python、原生 ES Modules、node:test、unittest、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-08-23-giga-resolved-link-cache-design.md`

## Global Constraints

- 权威 `catalog.json` 中的原始短链不得修改。
- 只发布 HTTPS 公共落地页，不发布 token、Cookie、Authorization 或媒体 CDN URL。
- 每条缓存必须绑定当前源 URL 的 SHA-256；源链接变化立即回退。
- 解析过程可断点恢复，单条失败不能破坏已有缓存或目录同步。
- Streamtape `/v/<id>/<filename>.mp4` 仍是 HTML 观看页，只能标记为 `external`。
- 未知域名、真人验证和异常页面失败关闭，不伪造成功。

---

### Task 1: Schema v2 与稳定链接 slot

**Files:**
- Modify: `public/js/resolved-links.js`
- Modify: `public/js/app.js`
- Modify: `tests/js/resolved-links.test.mjs`
- Modify: `tests/js/app.test.mjs`

**Interfaces:**
- Consumes: `code`, `slot`, `provider`, `sourceUrl`。
- Produces: schema v2 `Map<code\0slot, entry>`，普通版和无码版同提供商互不冲突。

- [ ] 写 schema v2、Streamtape HTML 观看页和双 slot 的失败测试。
- [ ] 运行针对性 Node 测试并确认因 v2/slot 未实现而失败。
- [ ] 实现 v2 校验、slot 传递和 source hash 回退。
- [ ] 运行针对性测试并确认通过。
- [ ] 提交 schema v2 前端改造。

### Task 2: 确定性队列、状态与 manifest 生成器

**Files:**
- Create: `src/giga_catalog/resolved_links.py`
- Create: `scripts/resolve_links.py`
- Create: `tests/python/test_resolved_links.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `public/data/catalog.json`、旧 manifest、非公开 state。
- Produces: `iter_catalog_candidates()`、`validate_final_url()`、`build_manifest()`、原子 JSON 写入和断点状态。

- [ ] 写目录遍历、allowlist、私网/凭据拒绝、确定性输出和旧结果保留的失败测试。
- [ ] 运行 Python 针对性测试并确认模块缺失失败。
- [ ] 实现最小核心与 CLI dry-run。
- [ ] 运行针对性测试并确认通过。
- [ ] 提交解析核心。

### Task 3: 本机持久 Chrome 全量采集器

**Files:**
- Create: `src/giga_catalog/resolved_links_browser.py`
- Modify: `scripts/resolve_links.py`
- Modify: `tests/python/test_resolved_links.py`

**Interfaces:**
- Consumes: pending candidate、Playwright persistent context。
- Produces: `verified/retryable/blocked-human/unsupported/dead` 结果；成功即时 checkpoint。

- [ ] 写正常两阶段按钮、弹窗关闭、未知目标和真人验证 fixture 的失败测试。
- [ ] 运行测试并确认 collector 接口缺失失败。
- [ ] 实现串行采集、限速、超时、checkpoint 和 `--max-links 0` 全量模式。
- [ ] 用三类真实样本做烟测并固定允许域名。
- [ ] 提交浏览器采集器。

### Task 4: 全量运行与每日同步

**Files:**
- Modify: `public/data/resolved-links.json`
- Modify: `.github/workflows/refresh-catalog.yml`
- Modify: `tests/python/test_deployment_config.py`
- Modify: release manifest generation inputs as required.

**Interfaces:**
- Consumes: 3122 条候选与断点状态。
- Produces: 全量已验证缓存；每日同步后运行无浏览器重定向补充并暂存该文件。

- [ ] 全量采集至 pending 队列耗尽或每条均有终态证据。
- [ ] 增加 workflow 失败关闭集成及失败测试。
- [ ] 运行完整 Python/JavaScript/静态与敏感字段扫描。
- [ ] 提交生成数据和自动同步。

### Task 5: 发布、浏览器验证与完成审计

**Files:**
- Verify: `public/data/resolved-links.json`
- Verify: GitHub Pages production deployment.

**Interfaces:**
- Consumes: main 分支部署。
- Produces: 正式站直达按钮、覆盖统计、失败分类和可回滚提交。

- [ ] 推送并等待 Deploy catalog 成功。
- [ ] 验证首屏懒加载、桌面与 390px、外链 rel、Console/Network。
- [ ] 对 manifest 每一条复算 source hash，并对 catalog 每一条链接核对覆盖或终态。
- [ ] 只有 3122 条全部有已验证直达结果时才宣告目标完成。
