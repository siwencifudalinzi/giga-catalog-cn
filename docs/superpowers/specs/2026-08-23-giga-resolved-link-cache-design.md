# GIGA 最终链接预解析与直达缓存设计

日期：2026-08-23
状态：待用户复核

## 1. 目标

把当前点击链路：

```text
GIGA Catalog → ouo.io → 等待/验证/广告 → Gofile 或 Streamtape
```

改为：

```text
GIGA Catalog → 已验证的最终公开落地页
```

解析在同步之后提前执行，用户点击时不再现场等待。解析失败、目标过期或出现真人验证时，保留原始短链作为显式回退，不影响现有目录、每日同步和链接追加保留语义。

本阶段的“最终链接”仅指公开的 Gofile/Streamtape 等落地页，不是带 Bearer Token、Cookie、签名或短期参数的媒体 CDN 地址。

## 2. 已验证事实

- 当前公开目录中的影片链接全部为 `ouo.io` 短链。
- 最新样本 `SPSF-58` 的 Gofile 短链可落到 `https://gofile.io/d/N87ugOtd`。
- Gofile API 能确认该落地页对应 `SPSF-58.mp4`，文件约 4.50 GiB，媒体端支持 `video/mp4`、Range 和 `206 Partial Content`。
- Gofile 媒体端要求临时 Authorization；不带令牌会跳回分享页，且媒体 CORS 仅允许 Gofile 自身来源。
- Gofile 分享页返回 `X-Frame-Options: DENY`。因此 GitHub Pages 不能把 Gofile 页面 iframe 到站内，也不能把临时 CDN 地址直接放入 `<video>`。
- ouo 的 Cloudflare/真人验证并非普通 301/302；无会话的 GitHub runner 不能保证解析。持久化真实 Chrome 会话可处理部分正常等待流程，但真人验证必须暂停等待用户处理。

## 3. 方案选择

### 3.1 采用：同步后预解析 + 持久缓存

解析分成两层：

1. **无浏览器解析器**：处理普通 HTTPS 重定向，运行在 CI 中。
2. **本地浏览器采集器**：使用用户机器上的持久 Chrome 配置，串行处理需要 JavaScript/倒计时的短链；只执行正常页面导航和明确的 `Get Link` 流程。出现真人验证、异常广告覆盖或未知页面结构时停止该来源，不伪造验证结果。

本地采集器优先处理最新影片和近期发生变化的链接；历史回填低速进行，不要求一次处理 3122 条，也不承诺历史链接达到 100% 覆盖。

### 3.2 不采用

- 点击时实时解析：慢、不稳定，失败会直接影响用户操作。
- GitHub Actions 批量跑无头浏览器：runner IP、全新会话和反自动化页面导致结果不可重复。
- 视频字节代理：单文件可达数 GiB，成本、带宽和故障面不可接受。
- 将 Gofile/Streamtape 临时媒体 URL 写入公开目录：地址短期有效且可能携带访问凭据。
- 自动解验证码或绕过第三方人机验证。

## 4. 数据模型

新增公开运行时文件：

```text
public/data/resolved-links.json
```

只包含已经验证的公开落地页：

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-08-23T00:00:00Z",
  "entries": {
    "SPSF-58": {
      "gofile": {
        "sourceUrlHash": "sha256:<64-lowercase-hex>",
        "finalUrl": "https://gofile.io/d/N87ugOtd",
        "kind": "external",
        "status": "verified",
        "checkedAt": "2026-08-23T00:00:00Z"
      }
    }
  }
}
```

约束：

- 公共文件不包含原始短链以外的新凭据、Authorization、Cookie、媒体 CDN URL、错误页面正文或浏览器日志。
- `sourceUrlHash` 绑定当前短链；表格更换短链后，旧解析结果立即失效并重新进入队列。
- `kind` 第一阶段只允许 `external`。后续单独评审后才允许 `embed` 或 `media`。
- `status` 只公开 `verified`；失败详情保存在非公开状态文件。

新增非公开状态：

```text
data/state/resolved-links-state.json
```

记录 `pending`、`blocked-human`、`retryable`、`dead`、重试次数、下次允许尝试时间、最终域名和错误分类，不进入 `public/`。

## 5. 解析与验证流程

```text
catalog.json 中的链接
→ 规范化并计算 sourceUrlHash
→ 命中仍有效缓存则复用
→ 普通 HTTPS 重定向解析
→ 必要时进入本地持久 Chrome 队列
→ 捕获最终导航
→ 安全与提供方验证
→ 原子生成 resolved-links.json
→ 运行发布校验
→ 部署
```

最终落地页验证规则：

- 必须为 HTTPS。
- 拒绝用户名/密码、fragment、超长 URL、`javascript:`、`data:`、HTTP 降级。
- 拒绝 localhost、私网、链路本地地址和解析到非公网 IP 的主机。
- 初始 allowlist：`gofile.io`、`www.gofile.io`、`streamtape.com`。
- Gofile 只接受 `/d/<public-id>`。
- Streamtape 第一阶段只接受公开观看页；不把 `/v/...mp4` 的文件名外观当成真实媒体。
- 最大重定向数、响应体大小、单链接超时和全局并发必须有硬上限。
- 验证失败不覆盖上一版已发布文件。

## 6. 增量队列策略

处理优先级：

1. 最近 14 天新增影片。
2. 原始短链刚发生变化的影片。
3. 尚未解析的历史影片，按最新到最旧低速回填。

默认每轮浏览器采集 20 条，串行执行；遇到真人验证时暂停整个来源，避免把几千条链接变成人工点击任务。成功结果立即写入候选状态，但只有完整校验通过后才原子发布。

失效处理：

- 公开落地页返回明确 404/410 或 API `error-notFound`：标记 `dead`，回退原始短链。
- 429/5xx/网络错误：指数退避，不删除上一版有效缓存。
- 原短链改变：旧最终地址不再用于点击。
- 目标仍可访问但文件已变化：只保留落地页，不信任文件名推导影片身份。

## 7. 前端交互

详情页和卡片链接按钮按以下顺序决定目标：

1. 命中 `resolved-links.json` 且 `sourceUrlHash` 与当前短链一致：显示“直达 Gofile”或“直达 Streamtape”。
2. 未命中：显示现有“打开 Gofile/Streamtape 中转链接”。
3. 已确认失效：显示“目标已失效”，同时保留原始链接的二级操作。

所有外链使用新标签页及 `rel="noopener noreferrer"`。最终 URL 不写入页面 URL、`data-*`、console 或错误文本；只在用户点击时从内存映射读取。

`resolved-links.json` 不参与首屏加载。它在打开影片详情或第一次点击外链时懒加载，避免抵消现有目录拆分带来的速度收益。

## 8. 与现有同步的集成

- `scripts/refresh.py` 和 `scripts/sync_official_tags.py` 的权威目录生成流程保持不变。
- 解析器在完整目录和标签事务成功后运行；解析失败不阻断影片目录更新。
- 解析缓存使用独立事务：先写候选文件，完成 schema、安全规则、确定性和 manifest 校验后再原子替换公开缓存；解析失败保留上一版缓存。解析器不得修改 `catalog.json` 内原始链接。
- 每天 03:17 的 `links-only` 和每天 11:30 的 `incremental` 继续保留；解析队列在链接同步成功后补充候选。
- 新文件加入部署 manifest、GitHub Actions 受限暂存路径和回滚清单。

普通重定向解析可随 GitHub Actions 自动运行。需要 JavaScript/倒计时的浏览器采集使用本机 Windows 计划任务：本机在线时启动持久 Chrome、更新候选缓存、运行校验，并通过当前 Git/GitHub 凭据提交和推送受限文件。凭据仅由本机 Git credential manager 使用，不写入脚本、缓存或前端。本机离线或遇到真人验证时跳过本轮解析，目录同步和既有缓存继续正常发布。

## 9. 安全与隐私边界

- 前端不出现 GitHub Token、浏览器 Cookie、Gofile guest token、Bearer Token 或 Chrome profile 数据。
- 浏览器 profile 只存在用户本机，加入 `.gitignore`，不得上传为 Actions artifact。
- 解析器只读取公开短链并记录最终公开落地页，不保存广告页 URL、追踪参数或弹窗地址。
- 未知域名永不自动跳转；只作为失败证据记录在非公开状态中。
- 不代理第三方视频字节，不自动解析受保护媒体地址，不绕过人机验证。

## 10. 测试与验收

### 单元测试

- URL 规范化、allowlist、私网/重绑定拒绝。
- sourceUrlHash 变化导致缓存失效。
- 普通重定向、循环、过多跳转、超时和响应过大。
- Gofile/Streamtape 路径分类。
- 缓存生成确定性、原子写入和失败保留上一版。

### 集成测试

- 使用本地 HTTP fixture 模拟 302 链、404、429、5xx 和未知域名。
- 浏览器采集器使用自有 fixture 模拟倒计时、弹窗和真人验证暂停，不依赖第三方站点作为 CI 测试。
- 解析任务失败时，影片目录仍能发布。

### 前端验收

- 已解析影片点击后直接进入最终公开落地页。
- 未解析影片仍能打开原始短链。
- 320、390、768、1440 px 无溢出。
- 键盘、触摸、焦点和 reduced-motion 正常。
- 首屏不请求 `resolved-links.json`，详情首次使用只请求一次。
- 页面、DOM、日志和公开错误信息不泄露临时媒体令牌。

## 11. 发布与回滚

第一阶段只对最新 20 条候选启用直达缓存。通过真实浏览器验收后逐步扩大到最近 100 条，再进入历史低速回填。

发布开关位于前端配置：关闭时完全忽略 `resolved-links.json`，恢复原始短链行为。回滚只需关闭开关或回退包含缓存文件的提交，不修改权威 `catalog.json`，因此不会破坏每日目录和链接同步。

## 12. 完成标准

- 最新 20 条中可解析的链接点击时不再经过 ouo。
- 遇到真人验证不会产生伪成功、死循环或批量弹窗。
- 解析失败不阻断目录发布，也不删除上一版有效结果。
- 首屏性能无回退。
- 不公开临时媒体 URL、令牌或浏览器状态。
- Gofile 保持外部直达；站内播放作为后续独立设计，不与本阶段混合。
