# GIGA 全量直达链接发布记录

日期：2026-08-24

## 数据结果

- Catalog 影片：2951
- 原始链接 slot：3122
- 已验证公开落地页：3030
  - Streamtape：2298
  - Player4me：625
  - Gofile：107
- 持续转向已停服/未支持终点：83
- Cloudflare 人机验证失败关闭：9
- 未分类或仍在重试的条目：0

92 个无可发布直达页的 slot 继续使用 `catalog.json` 中的原短链。它们不进入公开 manifest，不伪造成功，不解析受保护媒体地址。

## 安全边界

- 公开 manifest 只包含 allowlist 内的 HTTPS 落地页。
- 每条记录绑定 `code + slot + sourceUrlHash`。
- Streamtape `.mp4` 外观路径仍是 `external` HTML 观看页，没有当成 `<video src>`。
- 没有 Cookie、Authorization、Gofile token、Chrome profile、广告页 URL 或私有错误内容进入 `public/`。
- 未执行验证码解答、人机验证绕过、视频字节代理或受保护 CDN 地址解析。

## 验证证据

- Python：264 tests passed，1 skipped（Windows 无目录 symlink 权限）。
- JavaScript：72 tests passed。
- `node --check`：全部公开 JavaScript 通过。
- `git diff --check`：通过。
- 公开目录敏感字段扫描：无 token、Bearer、owner secret 或私钥命中。
- 全量交叉审计：3122 个 candidate = 3030 verified + 83 unsupported + 9 blocked-human；3030 个 manifest entry 的 source hash 和 provider/终点域全部匹配。

## 每日同步

Windows 计划任务每天 12:30 运行，一次最多处理 100 个新增/变更 slot，使用 4 个持久 Chrome profile。它只允许提交 `public/data/resolved-links.json`，没有变更时不产生提交，推送不使用 force。

## 回滚

回退本次数据提交即可恢复原短链行为。`catalog.json`、每日影片同步和原链接从未被改写。
