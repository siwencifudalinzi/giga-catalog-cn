# GIGA 单影片直达链接试点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 为 SPSF-58 的 Gofile 按钮增加一个可测试的公开落地页直达缓存，缓存缺失、损坏或源短链变化时继续使用原始 ouo 链接。

**Architecture:** 新增独立的 resolved-links.js，负责严格校验公开缓存、计算源 URL 的 SHA-256 并选择直达或回退地址。详情弹窗仍先同步渲染现有链接，随后仅在详情打开时懒加载缓存并替换匹配按钮；目录首屏、同步脚本和 catalog.json 完全不改。

**Tech Stack:** 原生 ES Modules、Web Crypto、Node.js node:test、静态 JSON、现有 Python/JavaScript 测试套件

**Spec:** docs/superpowers/specs/2026-08-23-giga-resolved-link-cache-design.md

## Global Constraints

- 试点只发布 SPSF-58 的 Gofile 公开落地页 https://gofile.io/d/N87ugOtd。
- 只允许 HTTPS、kind=external、status=verified 和明确 allowlist 的公开观看页。
- 不保存或发布 Bearer Token、Cookie、临时媒体 CDN URL、浏览器 profile 或第三方页面正文。
- sourceUrlHash 必须绑定当前 https://ouo.io/mT78vqU；源链接变化时自动回退原链。
- 缓存加载或校验失败不得影响目录渲染、每日同步和原始外链。
- resolved-links.json 不加入首屏 preload，只在影片详情需要时请求。
- 所有外链继续使用 target=_blank 和 rel=noopener noreferrer。

---

### Task 1: 可验证的直达缓存模块与单影片数据

**Files:**
- Create: public/js/resolved-links.js
- Create: public/data/resolved-links.json
- Create: tests/js/resolved-links.test.mjs

**Interfaces:**
- Consumes: resolved-links.json schema version 1；影片番号、provider 和当前源短链。
- Produces: normalizeResolvedLinkManifest(raw): Map、sha256SourceUrl(sourceUrl, subtle?): Promise<string>、resolveLinkTarget(input, manifest, subtle?): Promise<{url,label,resolved}>、createResolvedLinkLoader(fetcher?): () => Promise<Map>。

- [ ] **Step 1: 写 manifest 校验和源链接绑定的失败测试**

~~~js
import assert from "node:assert/strict";
import test from "node:test";
import {
  createResolvedLinkLoader,
  normalizeResolvedLinkManifest,
  resolveLinkTarget,
  sha256SourceUrl,
} from "../../public/js/resolved-links.js";

const SOURCE = "https://ouo.io/mT78vqU";
const HASH = "sha256:8e4a74b155b39a37bc851982ed6c75f3b6ee95f0b42528b11cc6cc62afe198fc";

test("hash binds the exact source URL", async () => {
  assert.equal(await sha256SourceUrl(SOURCE), HASH);
});

test("safe entry resolves direct and changed source falls back", async () => {
  const manifest = normalizeResolvedLinkManifest({
    schemaVersion: 1,
    entries: {
      "SPSF-58": {
        gofile: {
          sourceUrlHash: HASH,
          finalUrl: "https://gofile.io/d/N87ugOtd",
          kind: "external",
          status: "verified",
          checkedAt: "2026-08-23T00:00:00Z",
        },
      },
    },
  });
  assert.equal(manifest.size, 1);
  assert.deepEqual(
    await resolveLinkTarget(
      { code: "SPSF-58", provider: "gofile", label: "Gofile", sourceUrl: SOURCE },
      manifest,
    ),
    { url: "https://gofile.io/d/N87ugOtd", label: "直达 Gofile", resolved: true },
  );
  assert.deepEqual(
    await resolveLinkTarget(
      { code: "SPSF-58", provider: "gofile", label: "Gofile", sourceUrl: "https://ouo.io/changed" },
      manifest,
    ),
    { url: "https://ouo.io/changed", label: "Gofile", resolved: false },
  );
});

test("unsafe or media-looking destinations are dropped", () => {
  for (const finalUrl of [
    "http://gofile.io/d/N87ugOtd",
    "https://user:pass@gofile.io/d/N87ugOtd",
    "https://evil.example/d/N87ugOtd",
    "https://streamtape.com/v/file/movie.mp4",
  ]) {
    const manifest = normalizeResolvedLinkManifest({
      schemaVersion: 1,
      entries: {
        "SPSF-58": {
          gofile: {
            sourceUrlHash: HASH,
            finalUrl,
            kind: "external",
            status: "verified",
            checkedAt: "2026-08-23T00:00:00Z",
          },
        },
      },
    });
    assert.equal(manifest.size, 0);
  }
});

test("loader fetches once and network failure falls back empty", async () => {
  let calls = 0;
  const load = createResolvedLinkLoader(async () => {
    calls += 1;
    return { ok: true, json: async () => ({ schemaVersion: 1, entries: {} }) };
  });
  assert.equal((await load()).size, 0);
  assert.equal((await load()).size, 0);
  assert.equal(calls, 1);

  const failed = createResolvedLinkLoader(async () => {
    throw new Error("offline");
  });
  assert.equal((await failed()).size, 0);
});
~~~

- [ ] **Step 2: 运行测试并确认失败**

Run: node --test tests/js/resolved-links.test.mjs
Expected: FAIL，提示 public/js/resolved-links.js 不存在。

- [ ] **Step 3: 实现最小缓存模块**

实现以下明确规则：

~~~js
const ALLOWED_HOSTS = new Set(["gofile.io", "www.gofile.io", "streamtape.com"]);

function keyFor(code, provider) {
  return code + "\u0000" + provider;
}

function normalizeFinalUrl(value) {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.hash ||
      !ALLOWED_HOSTS.has(url.hostname)
    ) return null;
    if (
      ["gofile.io", "www.gofile.io"].includes(url.hostname) &&
      !/^\/d\/[A-Za-z0-9]+\/?$/u.test(url.pathname)
    ) return null;
    if (
      url.hostname === "streamtape.com" &&
      (/\.mp4(?:$|\/)/iu.test(url.pathname) ||
       !/^\/v\/[A-Za-z0-9_-]+(?:\/[^/]*)?\/?$/u.test(url.pathname))
    ) return null;
    return url.href;
  } catch {
    return null;
  }
}
~~~

normalizeResolvedLinkManifest 只接受 schemaVersion=1、provider 为 gofile/streamtape、kind=external、status=verified、sourceUrlHash 匹配 sha256 加 64 位小写 hex，以及 normalizeFinalUrl 成功的条目。

sha256SourceUrl 使用 TextEncoder 和 crypto.subtle.digest("SHA-256", bytes)。resolveLinkTarget 对当前 sourceUrl 计算 hash；一致时返回 finalUrl 与“直达 + provider label”，其余任意情况返回原 sourceUrl。createResolvedLinkLoader 缓存首次 Promise；HTTP 非 2xx、JSON 错误和网络失败均返回空 Map。

创建 public/data/resolved-links.json：

~~~json
{
  "schemaVersion": 1,
  "generatedAt": "2026-08-23T00:00:00Z",
  "entries": {
    "SPSF-58": {
      "gofile": {
        "sourceUrlHash": "sha256:8e4a74b155b39a37bc851982ed6c75f3b6ee95f0b42528b11cc6cc62afe198fc",
        "finalUrl": "https://gofile.io/d/N87ugOtd",
        "kind": "external",
        "status": "verified",
        "checkedAt": "2026-08-23T00:00:00Z"
      }
    }
  }
}
~~~

- [ ] **Step 4: 运行针对性测试**

Run: node --test tests/js/resolved-links.test.mjs
Expected: 4 tests PASS。

- [ ] **Step 5: 提交缓存模块**

~~~powershell
git add public/js/resolved-links.js public/data/resolved-links.json tests/js/resolved-links.test.mjs
git commit -m "feat: add single-video resolved link cache"
~~~

### Task 2: 详情页懒加载直达链接并保留回退

**Files:**
- Modify: public/js/app.js:1-10, 240-285, 1389-1413
- Modify: tests/js/app.test.mjs:20-45, 386-440

**Interfaces:**
- Consumes: Task 1 的 createResolvedLinkLoader() 和 resolveLinkTarget()。
- Produces: upgradeLinkGroups(videoCode, groups, manifest): Promise<Array<LinkGroup>>；详情弹窗打开后将匹配按钮升级为“直达 Gofile”。

- [ ] **Step 1: 写详情链接升级的失败测试**

把 upgradeLinkGroups 加入 app.test.mjs 的 import，并加入：

~~~js
test("resolved cache upgrades one matching link and preserves the other", async () => {
  const groups = collectLinkGroups({
    gofile: "https://ouo.io/mT78vqU",
    streamtape: "https://ouo.io/kPWPLr",
  });
  const manifest = new Map([
    ["SPSF-58\u0000gofile", {
      sourceUrlHash: "sha256:8e4a74b155b39a37bc851982ed6c75f3b6ee95f0b42528b11cc6cc62afe198fc",
      finalUrl: "https://gofile.io/d/N87ugOtd",
      kind: "external",
      status: "verified",
    }],
  ]);
  const upgraded = await upgradeLinkGroups("SPSF-58", groups, manifest);
  assert.deepEqual(upgraded[0].links, [
    {
      provider: "streamtape",
      label: "Streamtape",
      url: "https://ouo.io/kPWPLr",
      resolved: false,
    },
    {
      provider: "gofile",
      label: "直达 Gofile",
      url: "https://gofile.io/d/N87ugOtd",
      resolved: true,
    },
  ]);
});
~~~

扩展首屏测试：

~~~js
assert.doesNotMatch(html, /resolved-links\.json/u);
~~~

- [ ] **Step 2: 运行测试并确认失败**

Run: node --test tests/js/app.test.mjs
Expected: FAIL，提示 upgradeLinkGroups 未导出。

- [ ] **Step 3: 实现详情页异步升级**

在 app.js 导入 createResolvedLinkLoader 和 resolveLinkTarget，模块级创建 loadResolvedLinks，但不调用。upgradeLinkGroups 保持 group 和 link 顺序，对每个 link 调用：

~~~js
resolveLinkTarget(
  {
    code: videoCode,
    provider: item.provider,
    label: item.label,
    sourceUrl: item.url,
  },
  manifest,
)
~~~

把 createLinkSection 拆成 renderLinkSection(groups) 和 createLinkSection(video)。createLinkSection 先用原 groups 返回同步 DOM，然后异步 loadResolvedLinks → upgradeLinkGroups；仅当 section.isConnected 且至少一个 item.resolved 为 true 时 replaceWith(renderLinkSection(upgraded))。

renderLinkSection 创建 anchor 时继续设置：

~~~js
anchor.href = item.url;
anchor.target = "_blank";
anchor.rel = "noopener noreferrer";
~~~

不得把最终 URL 写入 data-*、console、页面查询参数或错误文本。

- [ ] **Step 4: 运行 JavaScript 测试**

Run: npm.cmd run test:js
Expected: 全部 JavaScript tests PASS。

- [ ] **Step 5: 运行完整回归与静态检查**

~~~powershell
npm.cmd test
node --check public/js/resolved-links.js
node --check public/js/app.js
git diff --check
~~~

Expected: 全部测试通过，node --check exit 0，git diff --check 无输出。

- [ ] **Step 6: 本地 HTTP 和真实浏览器烟测**

Run: py -m http.server 8000 --directory public

验收：
- 首屏 Network 没有 resolved-links.json。
- 打开 SPSF-58 详情后只请求一次 resolved-links.json。
- Gofile 按钮变成“直达 Gofile”，href 是 https://gofile.io/d/N87ugOtd。
- Streamtape 仍是 https://ouo.io/kPWPLr。
- anchor 保留 noopener noreferrer。
- 390px 无横向溢出，Console 无错误。

- [ ] **Step 7: 提交详情页试点**

~~~powershell
git add public/js/app.js tests/js/app.test.mjs
git commit -m "feat: upgrade matching links to cached destinations"
~~~

### Task 3: 发布前只读核验

**Files:**
- Verify only: public/data/resolved-links.json
- Verify only: public/js/resolved-links.js
- Verify only: public/js/app.js

**Interfaces:**
- Consumes: Task 1–2 的提交。
- Produces: 可供用户本地测试的分支状态；不合并主分支、不启用全量解析。

- [ ] **Step 1: 扫描公开目录中的敏感字段**

~~~powershell
Get-ChildItem public -Recurse -File |
  Select-String -Pattern 'Bearer\s+[A-Za-z0-9._-]+|Authorization|guestToken|access_token|refresh_token|Cookie:' -CaseSensitive:$false
~~~

Expected: 本次新增文件不包含令牌、Cookie 或 Authorization 值。

- [ ] **Step 2: 确认变更范围和工作树**

~~~powershell
git diff --check HEAD~2..HEAD
git diff --name-only HEAD~2..HEAD
git status --short
~~~

Expected: 只包含计划列出的五个实现/测试/数据文件，工作树为空。

- [ ] **Step 3: 记录试点边界**

交付报告明确写明：
- 目前只有 SPSF-58 → Gofile 使用直达。
- 当前落地页是外部 Gofile 页面，不是站内播放器。
- 没有自动批量解析、Windows 计划任务或 Streamtape 直达。
- 用户确认试点体验后，才进入全量解析器与自动同步阶段。
