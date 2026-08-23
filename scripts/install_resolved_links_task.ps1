param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = 'GIGA Resolved Links Daily'
)

$ErrorActionPreference = 'Stop'
$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$runner = Join-Path $RepoPath 'scripts/run_resolved_links_sync.ps1'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Runner missing: $runner"
}

$escapedRunner = $runner.Replace('"', '""')
$escapedRepo = $RepoPath.Replace('"', '""')
$action = New-ScheduledTaskAction `
    -Execute 'pwsh.exe' `
    -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"{0}`" -RepoPath `"{1}`"" -f $escapedRunner, $escapedRepo)
$trigger = New-ScheduledTaskTrigger -Daily -At '12:30'
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)
$principal = New-ScheduledTaskPrincipal `
    -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive `
    -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
