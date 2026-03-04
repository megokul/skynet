#Requires -Version 5
<#
.SYNOPSIS
    Maintains the canonical OpenClaw reverse SSH tunnel.

.DESCRIPTION
    Keeps EC2:<REMOTE_PORT> forwarding to local SSH (<LOCAL_PORT>) and
    reconnects automatically after disconnects.

    Canonical scheduled task name: OpenClawReverseTunnel
#>

param(
    [string]$Ec2Host = $env:OPENCLAW_TUNNEL_EC2_HOST,
    [string]$Ec2User = $env:OPENCLAW_TUNNEL_EC2_USER,
    [string]$SshKey = $env:OPENCLAW_TUNNEL_SSH_KEY,
    [int]$RemotePort = $(if ($env:OPENCLAW_TUNNEL_REMOTE_PORT) { [int]$env:OPENCLAW_TUNNEL_REMOTE_PORT } else { 2222 }),
    [int]$LocalPort = $(if ($env:OPENCLAW_TUNNEL_LOCAL_PORT) { [int]$env:OPENCLAW_TUNNEL_LOCAL_PORT } else { 22 }),
    [int]$RetryDelaySeconds = $(if ($env:OPENCLAW_TUNNEL_RETRY_DELAY) { [int]$env:OPENCLAW_TUNNEL_RETRY_DELAY } else { 10 }),
    [int]$ConnectTimeoutSeconds = $(if ($env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT) { [int]$env:OPENCLAW_TUNNEL_CONNECT_TIMEOUT } else { 10 }),
    [int]$HealthIntervalSeconds = $(if ($env:OPENCLAW_TUNNEL_HEALTH_INTERVAL) { [int]$env:OPENCLAW_TUNNEL_HEALTH_INTERVAL } else { 60 }),
    [string]$LogFile = $(if ($env:OPENCLAW_TUNNEL_LOG_FILE) { $env:OPENCLAW_TUNNEL_LOG_FILE } else { "$env:TEMP\openclaw_tunnel.log" }),
    [int]$LogRotateMb = 10,
    [int]$LogKeep = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Ec2Host) { $Ec2Host = "ec2-3-212-193-68.compute-1.amazonaws.com" }
if (-not $Ec2User) { $Ec2User = "ubuntu" }
if (-not $SshKey) { $SshKey = "$env:USERPROFILE\.ssh\protech-bot-key.pem" }

function Rotate-Log {
    param([string]$Path, [int]$MaxMb = 10, [int]$Keep = 5)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $maxBytes = [int64]$MaxMb * 1MB
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -lt $maxBytes) {
        return
    }
    for ($i = $Keep - 1; $i -ge 1; $i--) {
        $src = "$Path.$i"
        $dst = "$Path." + ($i + 1)
        if (Test-Path -LiteralPath $src) {
            Move-Item -LiteralPath $src -Destination $dst -Force
        }
    }
    Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
}

function Write-Log {
    param([string]$Message)
    Rotate-Log -Path $LogFile -MaxMb $LogRotateMb -Keep $LogKeep
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-RemoteBind {
    $remoteCheck = "sh -lc ""ss -ltn | awk '{print `$4}' | grep -E '[:.]$RemotePort$' >/dev/null && echo LISTEN || echo MISSING"""
    $checkArgs = @(
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=$ConnectTimeoutSeconds",
        "-i", $SshKey,
        "$Ec2User@$Ec2Host",
        $remoteCheck
    )
    try {
        $output = & ssh @checkArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            $line = ($output | Select-Object -First 1)
            if ($line -and $line.ToString().Trim().ToUpperInvariant() -eq "LISTEN") {
                return "LISTEN"
            }
        }
        return "MISSING"
    }
    catch {
        return "UNKNOWN"
    }
}

$mutexName = "Global\OpenClawReverseTunnel"
$mutexCreated = $false
$mutex = New-Object System.Threading.Mutex($true, $mutexName, [ref]$mutexCreated)
if (-not $mutexCreated) {
    Write-Host "Another keep_tunnel_alive.ps1 instance is already running. Exiting."
    exit 0
}

try {
    Write-Log "=== OpenClaw reverse tunnel keepalive started ==="
    Write-Log "TaskName=OpenClawReverseTunnel"
    Write-Log "Endpoint=$Ec2User@$Ec2Host"
    Write-Log "Tunnel=EC2:$RemotePort -> localhost:$LocalPort"
    Write-Log "SSH key=$SshKey"
    Write-Log "LogFile=$LogFile"

    while ($true) {
        $bind = Test-RemoteBind
        Write-Log "Pre-connect remote bind check: $bind"

        $sshArgs = @(
            "-N",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=$ConnectTimeoutSeconds",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "TCPKeepAlive=yes",
            "-i", $SshKey,
            "-R", "${RemotePort}:localhost:${LocalPort}",
            "${Ec2User}@${Ec2Host}"
        )

        Write-Log "Connecting tunnel..."
        $proc = Start-Process -FilePath "ssh" -ArgumentList $sshArgs -PassThru -NoNewWindow
        $startedAt = Get-Date
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds $HealthIntervalSeconds
            $bind = Test-RemoteBind
            $uptime = [int]((Get-Date) - $startedAt).TotalSeconds
            Write-Log "Heartbeat pid=$($proc.Id) uptime_s=$uptime remote_bind=$bind"
            $proc.Refresh()
        }

        $exitCode = $proc.ExitCode
        Write-Log "SSH exited (code $exitCode). Retrying in ${RetryDelaySeconds}s..."
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
finally {
    try {
        $mutex.ReleaseMutex() | Out-Null
    }
    catch {
    }
    $mutex.Dispose()
}
