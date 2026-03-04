#Requires -Version 5
<#
.SYNOPSIS
    Registers or updates the canonical OpenClaw reverse tunnel task.
#>

param(
    [string]$TaskName = "OpenClawReverseTunnel",
    [string]$ScriptPath = "$PSScriptRoot\keep_tunnel_alive.ps1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Tunnel script not found: $ScriptPath"
}

$arguments = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$ScriptPath`""
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Canonical OpenClaw reverse SSH tunnel keepalive task" `
    -Force | Out-Null

Write-Host "Registered task '$TaskName' -> $ScriptPath"

$staleTasks = Get-ScheduledTask | Where-Object {
    $_.TaskName -like "*OpenClaw*Tunnel*" -and $_.TaskName -ne $TaskName
}

foreach ($task in $staleTasks) {
    try {
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath | Out-Null
        Write-Host "Disabled stale tunnel task: $($task.TaskPath)$($task.TaskName)"
    }
    catch {
        Write-Warning "Could not disable stale task $($task.TaskName): $($_.Exception.Message)"
    }
}
