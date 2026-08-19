# GIGA Catalog

GIGA Catalog 是一个静态影片目录站。Python 刷新程序从 GIGA 官网同步影片元数据，并导入公开 Google Sheet 中的播放链接和明确标记的英文字幕资源；校验通过后生成 `public/data/catalog.json`。GitHub Actions 分别负责刷新提交和 GitHub Pages 生产部署。

原始导入、抓取状态、测试和日志都不在 Pages 发布目录内。浏览器也不会直接请求 Google Sheet。

## 环境要求

- Python 3.9 或更高版本
- Node.js 24 或更高版本
- Git
- GitHub CLI（首次建仓库和查看 Actions 时使用）

Windows PowerShell 初始化：

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS/Linux 初始化：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

JavaScript 测试只使用 Node.js 内置 test runner，当前没有需要安装的前端运行时依赖。

## 本地刷新

首次生成且仓库中尚无 `public/data/catalog.json` 时，刷新程序会从保留的旧目录导入种子数据。Windows 默认旧目录为 `D:\giga-catalog`，也可以显式指定：

```powershell
python scripts/refresh.py --mode links-only --legacy-dir D:\giga-catalog
```

常用命令：

```powershell
# 日常增量：读取最新目录页，同时全量重新导入公开表格
python scripts/refresh.py --mode incremental

# 完整目录复查；只有扫描完整且通过下降门禁时才允许删除旧记录
python scripts/refresh.py --mode audit

# 有边界的审计；边界外记录会保留
python scripts/refresh.py --mode audit --start-id 7000 --end-id 8000

# 只更新表格链接，不改影片元数据
python scripts/refresh.py --mode links-only

# 完成下载、合并和校验，但不写任何文件
python scripts/refresh.py --mode incremental --dry-run
```

可用 `python scripts/refresh.py --help` 查看超时、重试、抓取间隔、数据源地址和严格链接覆盖等参数。日常运行不建议开启 `--strict-links`：旧目录中确实存在没有外部链接的合法影片。

一次成功的非 dry-run 刷新会更新：

- `data/raw/products.json`
- `data/raw/sheet.csv`
- `data/raw/subtitles.json`
- `data/state/scrape-state.json`
- `data/update-summary.json`
- `public/data/catalog.json`

公开目录和私有状态按同一事务发布。下载、解析、校验或写入失败时，程序保留上一版文件。

播放和字幕链接使用追加保留（append-only overlay）语义：新表格中的空白或缺失单元格不会删除已验证的历史链接。当前没有隐式删除机制；未来如需删除，必须先设计并校验显式 tombstone。

字幕导入只信任主表中声明的 `#ff00ff` 粉色 ENGSUB 图例。只有明确粉色的系列来源才可以进入字幕流程：直连 Google Drive 可保留为系列级链接，显式粉色子表只在点名并校验具体番号后才下沉到影片。不透明短链只记录为私有的 unresolved 诊断，绝不复制到系列或影片。蓝、红、紫、橙色链接不是字幕权威。

## 测试和本地预览

与 Ubuntu Actions runner 一致的测试命令：

```bash
python -m unittest discover -s tests/python -v && npm run test:js
```

Windows 也可以运行项目快捷命令：

```powershell
npm.cmd test
```

启动本地静态服务器：

```powershell
python -m http.server 8000 --directory public
```

然后访问 [http://localhost:8000](http://localhost:8000)。不要直接双击 `public/index.html`，否则浏览器的本地文件限制可能阻止加载目录 JSON。

## 第一次推送到 GitHub

已经在 Chrome 登录 GitHub 不代表 GitHub CLI 自动获得授权，但不需要向命令行输入 GitHub 密码。使用浏览器 OAuth：

```powershell
gh auth login --web --git-protocol https
gh auth status
```

浏览器会打开 GitHub 授权页；如果 Chrome 已登录，通常只需确认授权。随后从仓库根目录创建私有仓库并推送，把 `OWNER/REPOSITORY` 换成实际名称：

```powershell
gh repo create OWNER/REPOSITORY --private --source=. --remote=origin --push
```

只有明确准备公开源代码和数据时才把 `--private` 改为 `--public`。如果远程仓库已经存在，则不要再次创建：

```powershell
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin HEAD
```

定时 workflow 必须存在于 GitHub 默认分支上才会生效。仓库或组织的 Actions 设置还必须允许 `GITHUB_TOKEN` 写入 Contents，生产分支保护也必须允许 `github-actions[bot]` 的这条受控数据提交路径；否则刷新可以通过，但最后的 `git push` 会被拒绝。

## GitHub Pages 生产站

生产站是 [https://siwencifudalinzi.github.io/giga-catalog-cn/](https://siwencifudalinzi.github.io/giga-catalog-cn/)。公开同步代码保存在 `siwencifudalinzi/giga-catalog-cn` 的 `master` 分支，只有 `public/` 会进入 Pages artifact；`data/raw`、`scripts`、`tests` 和仓库历史不会发布。原来的 `siwencifudalinzi/giga-catalog` 私有仓库继续保留，不会因为 Pages 发布而公开。

`.github/workflows/deploy-catalog.yml` 接受非 bot 对默认分支的 push、手动运行，或默认分支上的 `Refresh catalog` 成功结束。它始终重新 checkout 最新默认分支，运行完整 Python/JavaScript 测试，然后使用 GitHub 官方 `configure-pages`、`upload-pages-artifact` 和 `deploy-pages` actions 发布。发布不需要 Netlify Token 或其他部署密钥。

Pages 项目站位于 `/giga-catalog-cn/` 子路径。数据 preload、模块 fetch 和本地精选封面均按部署基址解析；在 GitHub Pages 上远程 GIGA 封面直接使用原始 HTTPS 地址，不再调用 Netlify Image CDN。`public/.nojekyll` 禁止 Jekyll 改写静态目录。

部署后检查：

```powershell
curl.exe -I https://siwencifudalinzi.github.io/giga-catalog-cn/
curl.exe -I https://siwencifudalinzi.github.io/giga-catalog-cn/data/catalog.json
curl.exe -I https://siwencifudalinzi.github.io/giga-catalog-cn/js/does-not-exist.js
```

首页和 catalog 必须返回 `200`，故意请求的缺失 JS 必须返回 `404`。

## 自动同步

`.github/workflows/refresh-catalog.yml` 只有三种触发方式：

- 每天 `03:17`（`Asia/Shanghai`）运行 `incremental`
- 每周日 `04:47`（`Asia/Shanghai`）运行完整 `audit`
- 在 GitHub Actions 页面手动运行

workflow 中的 cron 已换算为 UTC，用户看到的运行时间仍按上海时区计算。同一仓库和分支上的刷新使用并发组，不会取消正在写入状态的任务；GitHub 公共 runner 可能延迟定时任务几分钟，时间点不是实时 SLA。刷新 job 有默认分支 guard，负责刷新、验证和受限路径 bot 提交，不读取部署 secret，也不直接部署。成功结束后由独立 Pages workflow 发布最新 `public/`。

手动运行时可选择 `incremental`、`audit` 或 `links-only`。`start_id` 和 `end_id` 只允许用于手动 `audit`，并且必须是正整数。也可以使用 GitHub CLI：

```powershell
gh workflow run refresh-catalog.yml -f mode=incremental
gh workflow run refresh-catalog.yml -f mode=audit -f start_id=7000 -f end_id=8000
```

查看最近运行和日志：

```powershell
gh run list --workflow refresh-catalog.yml --limit 10
gh run list --workflow deploy-catalog.yml --limit 10
gh run watch RUN_ID
gh run view RUN_ID --log
```

手动运行 `Refresh catalog` 即可触发同一条刷新→Pages 部署链路；即使刷新没有产生 commit，成功的 `workflow_run` 仍会重新发布当前 `public/`。也可以单独手动运行 `Deploy catalog`。

每次刷新依次执行数据更新、完整 Python/JavaScript 测试、限路径暂存、有效变更检查和 bot 提交。它不会执行 `git add -A`，因此缓存、日志或意外源码改动不会混入自动数据提交。

网络下载只对连接异常和明确的短暂 HTTP 状态做有限次重试与间隔退避，达到上限后失败关闭，不会把不完整来源发布到线上。每次权威完整审计都会持久化 `lastAuditAt`、卡片对账计数、未解析诊断和历史档案保留证据；即使 catalog 内容未变，成功审计也会更新证据，后续增量运行则保留最近一次成功审计时间。

## 数据源限制

- GIGA 官网或 Google Sheet 暂时不可用、限流或改变 HTML/列结构时，刷新会失败并保留已部署版本；先查看 Actions 中失败步骤的完整日志。
- 默认目录下限为 `2007-12-07`，与旧站数据范围一致。
- 日常任务优先读取 GIGA 目录页，只在字段缺失、冲突或目录结构失效时使用详情页/尾部探测。
- 详情页能提供精确的连续预览图数量；旧目录记录的 `18` 只是兼容探测上限。前端先验证第 1 张，失败即显示“暂无预览”，不会继续请求后续图片。
- Google Sheet 每次全量导入，但播放链接只保存公开表格给出的原始 HTTP(S) 地址，不解析短链，也不保证第三方链接永久有效；空白单元格不会覆盖历史链接。
- 字幕导入仅访问公开主表和被明确粉色标记的子表；不跟踪不透明短链、不绕过访问控制，也不把旧 `drive_url` 当成当前权威。
- 并非每部影片都有链接，所以链接覆盖不足本身不等于影片数据损坏。
- GitHub 定时任务从默认分支最新提交运行；workflow 尚未合并到默认分支时不会自动执行。

## 故障处理与回滚

1. 分别查看 `Refresh catalog` 和 `Deploy catalog` Actions 日志，确认失败发生在下载、校验、测试、推送、artifact 上传还是 Pages 发布。
2. 如果新 deploy 有问题，回滚产生问题的提交；Pages workflow 会重新发布回滚后的 `public/`。
3. 对已经推送的错误数据提交使用可追踪的回滚，不要强制重置历史：

   ```powershell
   git log --oneline -- data public/data
   git revert BAD_COMMIT_SHA
   git push
   ```

4. 修复数据源或解析器后，从 Actions 页面手动运行一次 `incremental`；涉及完整性问题时运行 `audit`。部署链异常时单独重跑 `Deploy catalog`。
5. 原始 `D:\giga-catalog` 目录必须保持不变，它是额外的本地种子和回滚来源。

首次 Pages workflow 需要在仓库 Settings → Pages 中选择 `GitHub Actions` 作为 Source；之后每日刷新会自动发布。
