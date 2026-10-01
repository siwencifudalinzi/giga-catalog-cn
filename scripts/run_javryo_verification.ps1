param(
    [ValidateRange(1, 4)]
    [int]$BatchSize = 4,
    [int]$TimeoutMs = 30000,
    [switch]$Retry,
    [switch]$RetryPromising
)

$ErrorActionPreference = 'Stop'
$repo = (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $repo 'data/state/javryo-verification-run.log'
Set-Location -LiteralPath $repo
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$failures = 0

# Recycle the Playwright driver after one candidate per worker. Larger batches
# exhausted Node's 4 GB heap on the live player pages, losing the connection.

while ($true) {
    $verifierArgs = @('-X', 'utf8', 'scripts/verify_javryo_embeds.py', '--max-links', $BatchSize,
              '--timeout-ms', $TimeoutMs)
    if ($Retry) { $verifierArgs += '--retry' }
    if ($RetryPromising) { $verifierArgs += '--retry-promising' }
    $output = & 'C:\Windows\py.exe' @verifierArgs 2>&1
    $exit = $LASTEXITCODE
    $output | Add-Content -LiteralPath $log -Encoding UTF8
    if ($output -match '^queued=0$') { break }
    if ($exit -eq 0) {
        $failures = 0
    } else {
        $failures += 1
        if ($failures -ge 3) { throw "JAVRyo verifier failed three consecutive batches; inspect $log" }
    }
}
