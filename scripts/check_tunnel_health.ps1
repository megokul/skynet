#Requires -Version 5
<#
.SYNOPSIS
    Operator diagnostic for OpenClaw reverse SSH tunnel health.

.DESCRIPTION
    Validates that the canonical scheduled task owns the tunnel, verifies local
    ssh reverse-forward process visibility, and probes remote EC2 bind state.
#>

param(
    [string]$TaskName = "OpenClawReverseTunnel",
    [string]$Ec2Host = $env:OPENCLAW_TUNNEL_EC2_HOST,
    [string]$Ec2User = $env:OPENCLAW_TUNNEL_EC2_USER,
    [string]$SshKey = $env:OPENCLAW_TUNNEL_SSH_KEY,
    [int]$RemotePort = $(if ($env:OPENCLAW_TUNNEL_REMOTE_PORT) { [int]$env:OPENCLAW_TUNNEL_REMOTE_PORT } else { 2222 }),
    [int]$ConnectTimeoutSeconds = $(if ($env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT) { [int]$env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT } else { 10 }),
    [string]$CanonicalScriptPath = "$PSScriptRoot\keep_tunnel_alive.ps1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Emit-HealthResult {
    param(
        [string]$Category,
        [string]$Detail,
        [int]$ExitCode
    )
    Write-Host "health_category=$Category"
    if ($Detail) {
        Write-Host "health_detail=$Detail"
    }
    exit $ExitCode
}

if (-not $Ec2Host) { $Ec2Host = "ec2-3-212-193-68.compute-1.amazonaws.com" }
if (-not $Ec2User) { $Ec2User = "ubuntu" }

Write-Host "=== OpenClaw Tunnel Health Check ==="
Write-Host "TaskName: $TaskName"
Write-Host "Endpoint: $Ec2User@$Ec2Host"
Write-Host "RemotePort: $RemotePort"
Write-Host "SSH Key: $SshKey"
Write-Host "Canonical Script: $CanonicalScriptPath"

if (-not $SshKey) {
    Emit-HealthResult -Category "auth" -Detail "OPENCLAW_TUNNEL_SSH_KEY is required." -ExitCode 1
}
if (-not (Test-Path -LiteralPath $SshKey)) {
    Emit-HealthResult -Category "auth" -Detail "SSH key does not exist at '$SshKey'." -ExitCode 1
}
if (-not (Test-Path -LiteralPath $CanonicalScriptPath)) {
    Emit-HealthResult -Category "owner_mismatch" -Detail "Canonical script missing: '$CanonicalScriptPath'." -ExitCode 1
}

$resolvedCanonical = (Resolve-Path -LiteralPath $CanonicalScriptPath).Path

$ownerMismatch = $false
$ownerDetail = ""
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task: state=$($task.State) lastRun=$($taskInfo.LastRunTime) lastResult=$($taskInfo.LastTaskResult)"

    $action = $task.Actions | Select-Object -First 1
    if ($null -eq $action) {
        $ownerMismatch = $true
        $ownerDetail = "Task '$TaskName' has no action."
    }
    else {
        $taskArgs = [string]$action.Arguments
        $taskScriptPath = ""
        if ($taskArgs -match '-File\s+"([^"]+)"') {
            $taskScriptPath = [string]$matches[1]
        }
        elseif ($taskArgs -match "-File\s+([^\s]+)") {
            $taskScriptPath = [string]$matches[1]
            if ($taskScriptPath.StartsWith("'") -and $taskScriptPath.EndsWith("'")) {
                $taskScriptPath = $taskScriptPath.Trim("'")
            }
        }

        if (-not $taskScriptPath) {
            $ownerMismatch = $true
            $ownerDetail = "Task '$TaskName' action is missing -File script path."
        }
        elseif (-not (Test-Path -LiteralPath $taskScriptPath)) {
            $ownerMismatch = $true
            $ownerDetail = "Task '$TaskName' script does not exist: '$taskScriptPath'."
        }
        else {
            $resolvedTaskScript = (Resolve-Path -LiteralPath $taskScriptPath).Path
            Write-Host "Task script: $resolvedTaskScript"
            if ($resolvedTaskScript -ne $resolvedCanonical) {
                $ownerMismatch = $true
                $ownerDetail = "Task script '$resolvedTaskScript' does not match canonical '$resolvedCanonical'."
            }
        }
    }
}
catch {
    $ownerMismatch = $true
    $ownerDetail = "Task '$TaskName' not found or unreadable: $($_.Exception.Message)"
}

if ($ownerMismatch) {
    Emit-HealthResult -Category "owner_mismatch" -Detail $ownerDetail -ExitCode 1
}

$sshProcs = Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" |
    Where-Object { $_.CommandLine -match "-R\\s+${RemotePort}:" }
if ($sshProcs) {
    foreach ($proc in $sshProcs) {
        Write-Host "ssh.exe pid=$($proc.ProcessId) cmd=$($proc.CommandLine)"
    }
}
else {
    Write-Warning "No ssh.exe process found with reverse port $RemotePort."
}

$remoteCheck = "sh -lc ""ss -ltn | awk '{print `$4}' | grep -E '[:.]$RemotePort$' >/dev/null && echo LISTEN || echo MISSING"""
$sshArgs = @(
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=$ConnectTimeoutSeconds",
    "-i", $SshKey,
    "$Ec2User@$Ec2Host",
    $remoteCheck
)

$output = @()
$rc = 255
try {
    $output = & ssh @sshArgs 2>&1
    $rc = $LASTEXITCODE
}
catch {
    $output = @($_.Exception.Message)
    $rc = 255
}

$text = ($output | Out-String)
if ($null -eq $text) { $text = "" }
$text = $text.Trim()
$stateLine = [string]($output | Select-Object -First 1)
$state = $stateLine.Trim().ToUpperInvariant()
Write-Host "Remote bind probe: rc=$rc state=$state"
if ($text) {
    $preview = ($text -split "`r?`n" | Select-Object -First 6) -join " | "
    Write-Host "Remote probe output: $preview"
}

if ($rc -eq 0 -and $state -eq "LISTEN") {
    Emit-HealthResult -Category "healthy" -Detail "Tunnel bind is active on EC2 port $RemotePort." -ExitCode 0
}

if ($text -match "permission denied|publickey|authentication failed") {
    Emit-HealthResult -Category "auth" -Detail "SSH authentication failed while probing EC2." -ExitCode 1
}

if ($text -match "remote port forwarding failed|administratively prohibited|cannot listen port") {
    Emit-HealthResult -Category "port_conflict" -Detail "Remote forward could not bind port $RemotePort." -ExitCode 1
}

if ($text -match "could not resolve hostname|timed out|no route to host|connection refused|name or service not known|network is unreachable") {
    Emit-HealthResult -Category "ec2_unreachable" -Detail "Could not reach EC2 endpoint '$Ec2User@$Ec2Host'." -ExitCode 1
}

Emit-HealthResult -Category "remote_bind_missing" -Detail "SSH probe completed but remote bind state is not LISTEN." -ExitCode 1
