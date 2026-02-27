#Requires -Version 5
<#
.SYNOPSIS
    Keeps the reverse SSH tunnel to EC2 alive.
    Reconnects automatically whenever the connection drops.

.DESCRIPTION
    Run this at Windows startup via Task Scheduler.
    The tunnel maps EC2 port 2222 → this laptop's port 22 (OpenSSH).
    Docker on EC2 connects to host.docker.internal:2222 to reach your laptop.

.CONFIGURATION
    Edit the three variables below to match your setup.

.TASK SCHEDULER SETUP
    1. Open Task Scheduler → Create Task
    2. General tab:
       - Name: "OpenClaw SSH Tunnel"
       - Run whether user is logged on or not  (or just "when logged on" is fine)
       - Run with highest privileges: checked
    3. Triggers: At startup  OR  At log on
    4. Actions:
       Program:   powershell.exe
       Arguments: -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\path\to\keep_tunnel_alive.ps1"
    5. Settings:
       - If task is already running: Do not start a new instance
       - Stop task if it runs longer than: (clear / disable this)
#>

# ── CONFIGURE THESE ───────────────────────────────────────────────────────────
$EC2_HOST    = "ec2-3-212-193-68.compute-1.amazonaws.com"  # your EC2 hostname
$EC2_USER    = "ubuntu"                                     # EC2 SSH user
$SSH_KEY     = "$env:USERPROFILE\.ssh\protech-bot-key.pem"  # your private key to authenticate TO EC2
$REMOTE_PORT = 2222                                         # port EC2 listens on for reverse tunnel
$LOCAL_PORT  = 22                                           # your laptop's SSH port
# ── END CONFIG ────────────────────────────────────────────────────────────────

$RetryDelay  = 10   # seconds between reconnect attempts
$LogFile     = "$env:TEMP\openclaw_tunnel.log"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-Log "=== OpenClaw SSH Tunnel keepalive started ==="
Write-Log "  EC2:       $EC2_USER@$EC2_HOST"
Write-Log "  Tunnel:    EC2:$REMOTE_PORT -> localhost:$LOCAL_PORT"
Write-Log "  Key file:  $SSH_KEY"
Write-Log "  Log file:  $LogFile"

while ($true) {
    Write-Log "Connecting..."

    $sshArgs = @(
        "-N"                              # no remote command
        "-o", "StrictHostKeyChecking=no"  # accept new host keys automatically
        "-o", "ExitOnForwardFailure=yes"  # die if port-forward fails (e.g. port already in use)
        "-o", "ServerAliveInterval=30"    # keepalive ping every 30s
        "-o", "ServerAliveCountMax=3"     # drop after 3 missed pings (~90s)
        "-o", "TCPKeepAlive=yes"
        "-i", $SSH_KEY
        "-R", "${REMOTE_PORT}:localhost:${LOCAL_PORT}"
        "${EC2_USER}@${EC2_HOST}"
    )

    $proc = Start-Process -FilePath "ssh" -ArgumentList $sshArgs -PassThru -NoNewWindow -Wait
    $exitCode = $proc.ExitCode

    Write-Log "SSH exited (code $exitCode). Retrying in ${RetryDelay}s..."
    Start-Sleep -Seconds $RetryDelay
}
