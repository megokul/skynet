#Requires -Version 5
<#
.SYNOPSIS
    Registers or updates the canonical OpenClaw reverse tunnel task.
#>

param(
    [string]$TaskName = "OpenClawReverseTunnel",
    [string]$ScriptPath = "$PSScriptRoot\keep_tunnel_alive.ps1",
    [string]$Ec2Host = $env:OPENCLAW_TUNNEL_EC2_HOST,
    [string]$Ec2User = $env:OPENCLAW_TUNNEL_EC2_USER,
    [string]$SshKey = $env:OPENCLAW_TUNNEL_SSH_KEY,
    [string]$RemoteBindHost = $(if ($env:OPENCLAW_TUNNEL_REMOTE_BIND_HOST) { $env:OPENCLAW_TUNNEL_REMOTE_BIND_HOST } else { "0.0.0.0" }),
    [int]$RemotePort = $(if ($env:OPENCLAW_TUNNEL_REMOTE_PORT) { [int]$env:OPENCLAW_TUNNEL_REMOTE_PORT } else { 2222 }),
    [int]$LocalPort = $(if ($env:OPENCLAW_TUNNEL_LOCAL_PORT) { [int]$env:OPENCLAW_TUNNEL_LOCAL_PORT } else { 22 }),
    [bool]$StartNow = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Tunnel script not found: $ScriptPath"
}

try {
    $resolvedInput = (Resolve-Path -LiteralPath $ScriptPath).Path
}
catch {
    throw "Could not resolve script path '$ScriptPath': $($_.Exception.Message)"
}

$canonicalScriptPath = Join-Path -Path $PSScriptRoot -ChildPath "keep_tunnel_alive.ps1"
if (-not (Test-Path -LiteralPath $canonicalScriptPath)) {
    throw "Canonical tunnel script not found: $canonicalScriptPath"
}
$resolvedCanonical = (Resolve-Path -LiteralPath $canonicalScriptPath).Path
if ($resolvedInput -ne $resolvedCanonical) {
    Write-Warning "Ignoring non-canonical ScriptPath '$resolvedInput'. Using '$resolvedCanonical'."
}
$ScriptPath = $resolvedCanonical

if (-not $Ec2Host) { $Ec2Host = "ec2-3-212-193-68.compute-1.amazonaws.com" }
if (-not $Ec2User) { $Ec2User = "ubuntu" }
if (-not $SshKey) { $SshKey = $env:OPENCLAW_SSH_KEY_PATH }
if (-not $SshKey) {
    $canonicalKeyPath = "E:\MyProjects\skynet-key.pem"
    if (Test-Path -LiteralPath $canonicalKeyPath) {
        $SshKey = $canonicalKeyPath
    }
}
if (-not $SshKey) {
    throw "Missing SSH key. Set OPENCLAW_TUNNEL_SSH_KEY or OPENCLAW_SSH_KEY_PATH."
}
if (-not (Test-Path -LiteralPath $SshKey)) {
    throw "SSH key does not exist at '$SshKey'."
}

$arguments = @(
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$ScriptPath`"",
    "-Ec2Host", "`"$Ec2Host`"",
    "-Ec2User", "`"$Ec2User`"",
    "-SshKey", "`"$SshKey`"",
    "-RemoteBindHost", "`"$RemoteBindHost`"",
    "-RemotePort", "$RemotePort",
    "-LocalPort", "$LocalPort"
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
Write-Host "Tunnel args: ec2=$Ec2User@$Ec2Host bind=${RemoteBindHost}:$RemotePort key=$SshKey"

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

if ($StartNow) {
    try {
        $taskState = (Get-ScheduledTask -TaskName $TaskName).State
        if ($taskState -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
            Start-Sleep -Seconds 1
        }
        Start-ScheduledTask -TaskName $TaskName | Out-Null
        Write-Host "Started task '$TaskName'."
    }
    catch {
        Write-Warning "Could not start task '$TaskName': $($_.Exception.Message)"
        throw
    }
}
