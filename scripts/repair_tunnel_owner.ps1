#Requires -Version 5
<#
.SYNOPSIS
    One-command tunnel ownership repair for OpenClaw reverse SSH.

.DESCRIPTION
    Re-registers canonical scheduled task ownership, starts the task, and then
    runs health diagnostics.
#>

param(
    [string]$TaskName = "OpenClawReverseTunnel"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$registerScript = Join-Path -Path $PSScriptRoot -ChildPath "register_tunnel_task.ps1"
$healthScript = Join-Path -Path $PSScriptRoot -ChildPath "check_tunnel_health.ps1"

if (-not (Test-Path -LiteralPath $registerScript)) {
    throw "Missing script: $registerScript"
}
if (-not (Test-Path -LiteralPath $healthScript)) {
    throw "Missing script: $healthScript"
}

Write-Host "Repairing tunnel owner task '$TaskName'..."
& $registerScript -TaskName $TaskName -StartNow:$true

Write-Host "Running tunnel health diagnostics..."
& $healthScript -TaskName $TaskName
exit $LASTEXITCODE
