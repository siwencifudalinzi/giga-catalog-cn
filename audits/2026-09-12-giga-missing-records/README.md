# GIGA 缺失条目审计运行说明

本目录只保存审计脚本、只读来源证据和生成报告，不修改 `public/`、`data/` 或发布配置。脚本只读取元数据页面；不会打开播放或下载链接。

## 环境

- Python 3.9+
- 仓库已有 `requests` 依赖
- Node.js/npm 提供的 `npx`
- Playwright CLI（命令会通过 `npx --package @playwright/cli` 临时调用）

## 运行顺序

从仓库根目录执行：

```powershell
py -m unittest audits/2026-09-12-giga-missing-records/tests/test_audit.py -v
& 'audits/2026-09-12-giga-missing-records/crawl-asiamonstr.ps1'
py 'audits/2026-09-12-giga-missing-records/audit.py'
py 'audits/2026-09-12-giga-missing-records/verify_delisted.py'
```

`crawl-asiamonstr.ps1` 首次通过用户提供的 Google 跳转入口启动真实浏览器，然后只点击页面中实际显示的 `Next` 链接。每批保存进度到 `evidence/asiamonstr-progress.json`；同一浏览器会话中断后可直接续跑，浏览器会话丢失时脚本会从入口沿真实分页链接快进到断点。

`audit.py` 会重新获取线上 `catalog.json` 和当前 Google 表格，读取 Git 历史及 `D:\giga-catalog` 旧目录（存在时），并完整扫描 GIGA 官方目录。若刚完成官方扫描、只需要重新生成报告，可使用：

```powershell
py 'audits/2026-09-12-giga-missing-records/audit.py' --reuse-official
```

`verify_delisted.py` 对“历史条目，状态待核实”执行第二阶段核查。它只向有限数量的官网历史封面地址发送 HTTP `HEAD` 请求，不获取图片响应体；脚本可断点续跑，并把逐次响应元数据保存到 `evidence/delisted-asset-probes.json`。只有当前完整官网目录未收录、且官网资源返回 `200 image/*` 的条目，才会改列为“确认官方下架”。这里的“下架”仅指已从当前官网目录移除，不表示找到了官方公告或下架原因。

## 完成判定

- `evidence/asiamonstr-progress.json` 必须为 `completed: true`、`failure: null`。
- AsiaMonstr 每个分页都有 `evidence/asiamonstr-pages/NNNN.json`，末页必须没有 `nextUrl`。
- `evidence/official-summary.json` 必须为 `stopReason: "empty"`、`errors: 0`、`cardIntegrityComplete: true`。
- `series-coverage.csv` 必须包含 `baseline.json` 中的全部系列。
- `missing-records.csv` 每行必须具有五类之一及明确建议；单独空号、403、404、超时或未命中不能成为下架结论。

`baseline.json` 内嵌本次线上目录和精确响应字节的 SHA-256；原始响应另存为 `evidence/online-catalog.json`，便于复算。
