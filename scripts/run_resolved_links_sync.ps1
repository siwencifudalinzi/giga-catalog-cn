param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [int]$MaxLinks = 100,
    [int]$Workers = 4
)

$ErrorActionPreference = 'Stop'
$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$logPath = Join-Path $RepoPath 'data/state/resolved-links-task.log'
$beforePath = $null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null

function Write-TaskLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value ("{0} {1}" -f (Get-Date -Format o), $Message)
}

try {
    Set-Location -LiteralPath $RepoPath
    Write-TaskLog 'start'

    # The state file is the durable source. A prior interrupted run may leave only
    # this generated public file dirty; restore it before rebasing, then rebuild it.
    if (git status --porcelain -- public/data/resolved-links.json public/data/link-updates.json) {
        git restore --worktree -- public/data/resolved-links.json public/data/link-updates.json
    }
    git fetch origin
    git rebase origin/main

    $beforePath = [System.IO.Path]::GetTempFileName()
    Copy-Item -LiteralPath 'public/data/resolved-links.json' -Destination $beforePath -Force

    & 'C:\Windows\py.exe' scripts/resolve_links.py --browser --background-window --workers $Workers --max-links $MaxLinks --delay 0.5 --write
    if ($LASTEXITCODE -ne 0) { throw "resolver exited $LASTEXITCODE" }
    & 'C:\Windows\py.exe' scripts/update_link_changelog.py --source resolved --before $beforePath --after public/data/resolved-links.json --history public/data/link-updates.json
    if ($LASTEXITCODE -ne 0) { throw "changelog exited $LASTEXITCODE" }
    & 'C:\Windows\py.exe' -m unittest tests.python.test_resolved_links tests.python.test_link_updates -v
    if ($LASTEXITCODE -ne 0) { throw "resolved-link tests exited $LASTEXITCODE" }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw "git diff check exited $LASTEXITCODE" }

    git add -- public/data/resolved-links.json public/data/link-updates.json
    git diff --cached --quiet
    $hasChanges = $LASTEXITCODE -eq 1
    if ($LASTEXITCODE -gt 1) { throw "staged diff check exited $LASTEXITCODE" }
    if ($hasChanges) {
        git config user.name 'giga-resolved-links[bot]'
        git config user.email 'giga-resolved-links@users.noreply.github.com'
        git commit -m 'data: refresh resolved links'
        git push origin HEAD:main
        Write-TaskLog 'published'
    } else {
        Write-TaskLog 'unchanged'
    }
} catch {
    Write-TaskLog ("failed " + $_.Exception.GetType().Name)
    throw
} finally {
    if ($beforePath -and (Test-Path -LiteralPath $beforePath)) {
        Remove-Item -LiteralPath $beforePath -Force
    }
}
