param(
    [int]$BatchSize = 16,
    [int]$TimeoutMs = 30000,
    [switch]$Retry,
    [switch]$RetryPromising
)

$ErrorActionPreference = 'Stop'
$repo = (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $repo 'data/state/javryo-verification-run.log'
Set-Location -LiteralPath $repo
$env:PYTHONIOENCODING = 'utf-8'
$failures = 0

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
