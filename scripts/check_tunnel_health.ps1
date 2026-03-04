#Requires -Version 5
<#
.SYNOPSIS
    Operator diagnostic for OpenClaw reverse SSH tunnel health.
#>

param(
    [string]$TaskName = "OpenClawReverseTunnel",
    [string]$Ec2Host = $env:OPENCLAW_TUNNEL_EC2_HOST,
    [string]$Ec2User = $env:OPENCLAW_TUNNEL_EC2_USER,
    [string]$SshKey = $env:OPENCLAW_TUNNEL_SSH_KEY,
    [int]$RemotePort = $(if ($env:OPENCLAW_TUNNEL_REMOTE_PORT) { [int]$env:OPENCLAW_TUNNEL_REMOTE_PORT } else { 2222 }),
    [int]$ConnectTimeoutSeconds = $(if ($env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT) { [int]$env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT } else { 10 })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Ec2Host) { $Ec2Host = "ec2-3-212-193-68.compute-1.amazonaws.com" }
if (-not $Ec2User) { $Ec2User = "ubuntu" }
if (-not $SshKey) { $SshKey = "$env:USERPROFILE\.ssh\protech-bot-key.pem" }

Write-Host "=== OpenClaw Tunnel Health Check ==="
Write-Host "TaskName: $TaskName"
Write-Host "Endpoint: $Ec2User@$Ec2Host"
Write-Host "RemotePort: $RemotePort"
Write-Host "SSH Key: $SshKey"

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task: state=$($task.State) lastRun=$($taskInfo.LastRunTime) lastResult=$($taskInfo.LastTaskResult)"
}
catch {
    Write-Warning "Task '$TaskName' not found."
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

try {
    $output = & ssh @sshArgs
    $rc = $LASTEXITCODE
    $state = ($output | Select-Object -First 1).ToString().Trim()
    Write-Host "Remote bind probe: rc=$rc state=$state"
    if ($rc -ne 0 -or $state -ne "LISTEN") {
        exit 1
    }
}
catch {
    Write-Error "Remote bind probe failed: $($_.Exception.Message)"
    exit 1
}

Write-Host "Tunnel health check passed."
