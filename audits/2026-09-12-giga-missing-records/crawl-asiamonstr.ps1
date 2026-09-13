param(
    [string]$Session = "giga-audit",
    [int]$BatchSize = 10,
    [int]$DelayMilliseconds = 750,
    [int]$MaxPages = 500
)

$ErrorActionPreference = "Stop"
$auditRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$evidenceRoot = Join-Path $auditRoot "evidence"
$pageRoot = Join-Path $evidenceRoot "asiamonstr-pages"
$progressPath = Join-Path $evidenceRoot "asiamonstr-progress.json"
$recordsPath = Join-Path $evidenceRoot "asiamonstr-records.jsonl"
$entry = "https://www.asiamonstr.com/category/giga"
$browserEntry = "https://www.google.com.hk/goto?url=CAESYAHrOzAVa4unAbC7X7yMbEnIFk4AUJx2rVSuHzWNvdcZa-dXeq1G1or896qnSCUQ8H10QHF5RF998FfM4FB3iMX9Rc0ziaO7DvttZKj-d0bi-ZLYi-HvDh5J4y6jRJGaLQ"

[System.IO.Directory]::CreateDirectory($pageRoot) | Out-Null

if (Test-Path -LiteralPath $progressPath) {
    $progress = Get-Content -Raw -LiteralPath $progressPath | ConvertFrom-Json
} else {
    $progress = [pscustomobject]@{
        entryUrl = $entry
        nextUrl = $entry
        visitedUrls = @()
        completed = $false
        failure = $null
    }
}

if ($progress.completed) {
    Write-Output "AsiaMonstr traversal already complete: $($progress.visitedUrls.Count) pages"
    exit 0
}

$list = (& npx --yes --package '@playwright/cli' playwright-cli --json --raw list) | Out-String
if ($list -notmatch [regex]::Escape($Session)) {
    & npx --yes --package '@playwright/cli' playwright-cli "-s=$Session" open $browserEntry | Out-Null
}

$visited = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($url in $progress.visitedUrls) { [void]$visited.Add([string]$url) }
$allNewRecords = [System.Collections.Generic.List[string]]::new()
$pageCount = $visited.Count

while ($progress.nextUrl -and -not $progress.completed -and $pageCount -lt $MaxPages) {
    $startUrl = [string]$progress.nextUrl
    if ($visited.Contains($startUrl)) {
        $progress.failure = "pagination loop at $startUrl"
        break
    }

    $escapedUrl = $startUrl.Replace("'", "\'")
    $code = @"
async (page) => {
  const pages = [];
  let nextUrl = '$escapedUrl';
  let resumeHops = 0;
  while (page.url() !== nextUrl && resumeHops < $MaxPages) {
    const resumeLink = page.getByRole('link', {name: 'Next', exact: true});
    if (!await resumeLink.count()) throw new Error('cannot resume from ' + page.url() + ' to ' + nextUrl);
    await Promise.all([
      page.waitForURL(await resumeLink.getAttribute('href'), {waitUntil: 'domcontentloaded', timeout: 45000}),
      resumeLink.click()
    ]);
    resumeHops += 1;
    await page.waitForTimeout(100);
  }
  for (let i = 0; i < $BatchSize && nextUrl; i++) {
    if (page.url() !== nextUrl) {
      throw new Error('browser is at ' + page.url() + ' but resume cursor is ' + nextUrl);
    }
    await page.waitForTimeout($DelayMilliseconds);
    const pageUrl = page.url();
    const title = await page.title();
    const articles = await page.locator('article').evaluateAll(nodes => nodes.map(article => {
      const heading = article.querySelector('h2')?.textContent?.trim() || '';
      const detail = Array.from(article.querySelectorAll('a')).find(a => /\/giga\/.*\.html(?:$|[?#])/.test(a.href));
      const info = Array.from(article.querySelectorAll('.info div')).map(node => node.textContent.trim());
      const articleDate = info.find(value => /[A-Za-z]+\s+\d{1,2},\s+\d{4}/.test(value)) || '';
      return {heading, detailUrl: detail?.href || '', articleDate, excerpt: article.innerText.trim()};
    }));
    if (articles.length === 0) throw new Error('no category articles rendered at ' + pageUrl + ' (' + title + ')');
    const nextLink = page.getByRole('link', {name: 'Next', exact: true});
    const next = await nextLink.count() ? await nextLink.getAttribute('href') : '';
    pages.push({url: pageUrl, status: 'rendered-ok', title, articleCount: articles.length, articles, nextUrl: next});
    nextUrl = next;
    if (nextUrl) {
      await Promise.all([
        page.waitForURL(nextUrl, {waitUntil: 'domcontentloaded', timeout: 45000}),
        nextLink.click()
      ]);
      await page.waitForTimeout($DelayMilliseconds);
    }
  }
  return {pages, nextUrl};
}
"@
    # Windows PowerShell forwards embedded newlines to npx in a form that the
    # CLI JavaScript parser treats as separate arguments. Keep the snippet as
    # one explicit argument while preserving normal spaces inside selectors.
    $code = $code -replace "\r?\n", " "

    try {
        $outerText = (& npx --yes --package '@playwright/cli' playwright-cli --json --raw "-s=$Session" run-code "$code") | Out-String
        $outer = $outerText | ConvertFrom-Json
        if ($outer.isError) { throw "Playwright CLI: $($outer.error)" }
        $batch = $outer.result | ConvertFrom-Json
        if (-not $batch.pages -or $batch.pages.Count -eq 0) { throw "browser batch returned no pages" }

        foreach ($page in $batch.pages) {
            if ($visited.Contains([string]$page.url)) { throw "pagination loop at $($page.url)" }
            [void]$visited.Add([string]$page.url)
            $pageCount += 1
            $ordinal = "{0:D4}" -f $pageCount
            $page | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $pageRoot "$ordinal.json") -Encoding utf8
            foreach ($article in $page.articles) {
                $record = [ordered]@{
                    pageUrl = $page.url
                    pageOrdinal = $pageCount
                    heading = $article.heading
                    detailUrl = $article.detailUrl
                    articleDate = $article.articleDate
                    excerpt = $article.excerpt
                }
                $allNewRecords.Add(($record | ConvertTo-Json -Compress))
            }
        }
        $progress.nextUrl = [string]$batch.nextUrl
        $progress.visitedUrls = @($visited)
        $progress.completed = -not [bool]$batch.nextUrl
        $progress.failure = $null
        $progress | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $progressPath -Encoding utf8
        if ($allNewRecords.Count -gt 0) {
            [System.IO.File]::AppendAllLines($recordsPath, $allNewRecords, [System.Text.UTF8Encoding]::new($false))
            $allNewRecords.Clear()
        }
    } catch {
        $progress.failure = $_.Exception.Message
        $progress.visitedUrls = @($visited)
        $progress | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $progressPath -Encoding utf8
        throw
    }
}

if ($pageCount -ge $MaxPages -and -not $progress.completed) {
    $progress.failure = "maximum page limit $MaxPages reached before the real last page"
    $progress | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $progressPath -Encoding utf8
    throw $progress.failure
}

Write-Output "AsiaMonstr pages=$pageCount complete=$($progress.completed) next=$($progress.nextUrl)"
