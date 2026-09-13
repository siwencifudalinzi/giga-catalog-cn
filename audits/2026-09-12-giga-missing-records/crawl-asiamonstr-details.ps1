param(
    [string]$Session = "giga-archived-import",
    [int]$BatchSize = 8,
    [int]$DelayMilliseconds = 350,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$auditRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$inputPath = Join-Path $auditRoot "confirmed-delisted.csv"
$outputPath = Join-Path $auditRoot "evidence\asiamonstr-delisted-details.json"
$rows = @(Import-Csv -LiteralPath $inputPath)
$saved = if (-not $Restart -and (Test-Path -LiteralPath $outputPath)) {
    Get-Content -Raw -LiteralPath $outputPath | ConvertFrom-Json -AsHashtable
} else {
    @{ records = @{} }
}
if (-not $saved.records) { $saved.records = @{} }

$list = (& npx --yes --package '@playwright/cli' playwright-cli --json --raw list) | Out-String
if ($list -notmatch [regex]::Escape($Session)) {
    $browserEntry = "https://www.google.com.hk/goto?url=CAESYAHrOzAVa4unAbC7X7yMbEnIFk4AUJx2rVSuHzWNvdcZa-dXeq1G1or896qnSCUQ8H10QHF5RF998FfM4FB3iMX9Rc0ziaO7DvttZKj-d0bi-ZLYi-HvDh5J4y6jRJGaLQ"
    & npx --yes --package '@playwright/cli' playwright-cli "-s=$Session" open $browserEntry | Out-Null
}

$pending = @($rows | Where-Object { -not $saved.records.ContainsKey($_.'规范番号') })
for ($offset = 0; $offset -lt $pending.Count; $offset += $BatchSize) {
    $end = [Math]::Min($offset + $BatchSize - 1, $pending.Count - 1)
    $items = @($pending[$offset..$end] | ForEach-Object {
        [ordered]@{ code = $_.'规范番号'; detailUrl = $_.'AsiaMonstr详情页' }
    })
    $itemJson = $items | ConvertTo-Json -Compress
    $code = @"
async (page) => {
  const items = $itemJson;
  const records = [];
  for (const item of items) {
    try {
      const response = await page.goto(item.detailUrl, {waitUntil: 'domcontentloaded', timeout: 45000});
      await page.waitForTimeout($DelayMilliseconds);
      const article = page.locator('article').first();
      const allImages = await article.locator('a[href*="/wp-content/uploads/"]').evaluateAll(nodes => nodes.map(node => node.href));
      records.push({code: item.code, detailUrl: item.detailUrl, finalUrl: page.url(), httpStatus: response?.status() || 0, status: response?.status() === 200 && allImages.length > 1 ? 'ok' : 'no-preview-images', coverImage: allImages[0] || '', previewImages: allImages.slice(1)});
    } catch (error) {
      records.push({code: item.code, detailUrl: item.detailUrl, finalUrl: page.url(), status: 'error', error: String(error), coverImage: '', previewImages: []});
    }
  }
  return records;
}
"@
    $code = $code -replace "\r?\n", " "
    $outerText = (& npx --yes --package '@playwright/cli' playwright-cli --json --raw "-s=$Session" run-code "$code") | Out-String
    $outer = $outerText | ConvertFrom-Json
    if ($outer.isError) { throw "Playwright CLI: $($outer.error)" }
    $records = $outer.result | ConvertFrom-Json
    foreach ($record in $records) {
        $saved.records[[string]$record.code] = $record
    }
    $saved.method = "Browser-rendered AsiaMonstr article; first upload image is cover, all remaining article upload images are previews"
    $saved.expectedCount = $rows.Count
    $saved.completedCount = $saved.records.Count
    $saved.updatedAt = [DateTimeOffset]::Now.ToString("o")
    $saved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outputPath -Encoding utf8
    Write-Output "details $($saved.records.Count)/$($rows.Count)"
}

$failures = @($saved.records.Values | Where-Object { $_.status -ne 'ok' })
if ($saved.records.Count -ne $rows.Count -or $failures.Count -gt 0) {
    throw "detail crawl incomplete: records=$($saved.records.Count)/$($rows.Count), failures=$($failures.Count)"
}
