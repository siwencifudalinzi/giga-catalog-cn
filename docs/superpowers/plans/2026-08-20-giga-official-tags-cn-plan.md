# GIGA Official Chinese Tags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完整同步 GIGA 官网标签，建立中文标签索引、搜索与组合筛选，并将全部影片回填后发布。

**Architecture:** Python 同步层解析官网标签目录与详情页，以稳定 tag ID 归一化存储；构建层生成顶层标签字典和影片 `tagIds`。前端在内存中构建倒排索引，不引入框架或运行时翻译服务。

**Tech Stack:** Python 3 + requests + unittest，原生 ES modules + Node test runner，原生 HTML/CSS，Playwright 真实浏览器验收。

**Spec:** `docs/superpowers/specs/2026-08-20-giga-official-tags-cn-design.md`

## Global Constraints

- 官网标签 ID 是唯一关联键，译名不能成为关联键。
- 任何详情页或翻译失败都不得清空已有标签。
- 现有每日同步、链接合并、字幕同步、收藏和公共 catalog 必须向后兼容。
- 公开数据只含官网公开标签，不含个人标签或私有 URL。

---

### Task 1: 官网标签解析与会话修复

**Files:**
- Create: `src/giga_catalog/tags.py`
- Modify: `src/giga_catalog/scraper.py`
- Test: `tests/python/test_tags.py`
- Test: `tests/fixtures/product_tags.html`

**Interfaces:**
- Produces: `parse_product_tags(html: str) -> list[dict]`
- Produces: `parse_tag_directory(html: str, group: str) -> list[dict]`
- Produces: `fetch_product_detail(session, product_id, *, timeout) -> dict | None`

- [ ] 先写失败测试：两类标签、重复 ID、非标签链接和必需 Referer。
- [ ] 运行 `py -m unittest tests.python.test_tags -v`，确认因接口不存在失败。
- [ ] 实现最小容错解析和会话请求。
- [ ] 再运行针对测试和原有 `test_scraper.py`。

### Task 2: 标签归一化、中文字典与 catalog 构建

**Files:**
- Create: `data/tag-translation-overrides.json`
- Modify: `src/giga_catalog/tags.py`
- Modify: `src/giga_catalog/merge.py`
- Modify: `src/giga_catalog/validation.py`
- Test: `tests/python/test_tags.py`
- Test: `tests/python/test_merge.py`
- Test: `tests/python/test_validation.py`

**Interfaces:**
- Consumes: `list[dict]` 官方标签。
- Produces: `build_public_tag_index(products, definitions) -> list[dict]`
- Produces: catalog 顶层 `tags` 和影片 `tagIds`。

- [ ] 先写失败测试：ID 唯一、中文映射优先级、计数、未解析引用拒绝发布。
- [ ] 运行针对测试确认 RED。
- [ ] 实现归一化、构建和验证。
- [ ] 运行针对测试确认 GREEN。

### Task 3: 断点续传的全量回填与每日增量

**Files:**
- Create: `scripts/sync_official_tags.py`
- Modify: `scripts/refresh.py`
- Modify: `.github/workflows/refresh-catalog.yml`
- Test: `tests/python/test_sync_official_tags.py`
- Test: `tests/python/test_refresh.py`

**Interfaces:**
- Produces: `data/raw/tags.json`、带 `tagIds/tagsUpdatedAt` 的 `data/raw/products.json` 和 `public/data/catalog.json`。

- [ ] 先写失败测试：断点恢复、失败保留、新片增量、空标签成功状态。
- [ ] 运行针对测试确认 RED。
- [ ] 实现并发受限抓取、原子检查点和日常集成。
- [ ] 运行针对测试确认 GREEN。

### Task 4: 前端标签模型与筛选

**Files:**
- Create: `public/js/tags.js`
- Modify: `public/js/catalog.js`
- Test: `tests/js/tags.test.mjs`
- Test: `tests/js/catalog.test.mjs`

**Interfaces:**
- Produces: `createTagIndex(tags, videos)`
- Produces: `filterVideos({ include, exclude, match })`
- Extends: `model.getTags()`, `model.filterByTags(...)`

- [ ] 先写失败测试：中/日搜索、AND/OR/排除、稳定排序。
- [ ] 运行 `node --test tests/js/tags.test.mjs tests/js/catalog.test.mjs` 确认 RED。
- [ ] 实现无 DOM 模型并连接 catalog 搜索。
- [ ] 运行针对测试确认 GREEN。

### Task 5: 标签索引 UI 与详情标签

**Files:**
- Modify: `public/index.html`
- Modify: `public/js/app.js`
- Modify: `public/js/render.js`
- Modify: `public/css/style.css`
- Test: `tests/js/app.test.mjs`
- Test: `tests/js/catalog.test.mjs`

- [ ] 先写失败测试：标签 Tab、已选筹码、详情完整标签、不将名称塞入不必要的 `data-*`。
- [ ] 运行 JS 测试确认 RED。
- [ ] 实现键盘可用、手机可用的索引和筛选界面。
- [ ] 运行 JS 测试确认 GREEN。

### Task 6: 全量同步、验收与发布

**Files:**
- Modify: `data/raw/products.json`
- Create/Modify: `data/raw/tags.json`
- Modify: `public/data/catalog.json`
- Modify: `README.md`

- [ ] 运行全量标签回填，重试失败项，直到所有 3788 部影片都有完成状态。
- [ ] 校验全部 `tagIds` 存在于顶层字典，计数与倒排索引一致。
- [ ] 运行 `npm.cmd test`、`git diff --check` 和所有 `node --check`。
- [ ] 本地 HTTP 启动后在 320/390/768/1440px 及 200% 缩放验证标签索引、筛选、详情和溢出。
- [ ] 提交、推送、合并到 `main`，等待 GitHub Pages 部署并对生产站重复数据与浏览器验收。
