param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$EnvFile = '.env.worker-agent',
    [string]$TaskName = 'OpenClawWorkerAgent'
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

function Import-EnvFile {
    param([string]$Path)
    $result = @{}
    if (-not (Test-Path $Path)) { return $result }
    foreach ($rawLine in Get-Content $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
        $parts = $line.Split('=', 2)
        $result[$parts[0].Trim()] = $parts[1]
    }
    return $result
}

function Resolve-StatusUrl {
    param([hashtable]$EnvMap)
    if ($EnvMap.ContainsKey('SKYNET_GATEWAY_STATUS_URL') -and $EnvMap['SKYNET_GATEWAY_STATUS_URL']) {
        return $EnvMap['SKYNET_GATEWAY_STATUS_URL']
    }
    $gatewayUrl = $EnvMap['SKYNET_GATEWAY_URL']
    if (-not $gatewayUrl) { return '' }
    $uri = [System.Uri]$gatewayUrl
    $builder = [System.UriBuilder]::new($uri)
    $builder.Scheme = if ($builder.Scheme -eq 'wss') { 'https' } else { 'http' }
    $builder.Port = if ($uri.Port -eq 8765) { 8766 } else { $uri.Port }
    $builder.Path = '/status'
    return $builder.Uri.AbsoluteUri
}

function Test-TcpPort {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutMs = 1000
    )
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            $client.Close()
            return $false
        }
        $client.EndConnect($iar)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

$envMap = Import-EnvFile -Path (Join-Path $RepoRoot $EnvFile)
$workerId = if ($envMap['SKYNET_WORKER_ID']) { $envMap['SKYNET_WORKER_ID'] } else { 'worker-primary' }
$logDir = if ($envMap['SKYNET_AGENT_LOG_MIRROR_DIR']) { $envMap['SKYNET_AGENT_LOG_MIRROR_DIR'] } else { 'E:\MyProjects\skynet\logs' }
$statusUrl = Resolve-StatusUrl -EnvMap $envMap
$tunnelEnabled = $false
if ($envMap.ContainsKey('SKYNET_AGENT_TUNNEL_ENABLED')) {
    $tunnelEnabled = @('1','true','yes','on') -contains (($envMap['SKYNET_AGENT_TUNNEL_ENABLED'] + '').ToLowerInvariant())
}
$localWsPort = 18765
if ($envMap.ContainsKey('SKYNET_AGENT_TUNNEL_LOCAL_WS_PORT') -and $envMap['SKYNET_AGENT_TUNNEL_LOCAL_WS_PORT']) {
    [void][int]::TryParse($envMap['SKYNET_AGENT_TUNNEL_LOCAL_WS_PORT'], [ref]$localWsPort)
}
$localWsReady = $false
if ($tunnelEnabled) {
    $localWsReady = Test-TcpPort -TargetHost '127.0.0.1' -Port $localWsPort -TimeoutMs 1200
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host 'health_category=task_missing'
    exit 1
}
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName

$logWritable = $false
try {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $probe = Join-Path $logDir '.worker_agent_write_probe'
    'ok' | Set-Content $probe
    Remove-Item $probe -Force
    $logWritable = $true
} catch {
    $logWritable = $false
}

$gatewayOk = $false
$gatewayPayload = $null
if ($statusUrl) {
    try {
        $gatewayPayload = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 10
        $gatewayOk = $true
    } catch {
        $gatewayOk = $false
    }
}

$health = 'healthy'
if ($task.State -ne 'Ready' -and $task.State -ne 'Running') {
    $health = 'task_stopped'
} elseif (-not $logWritable) {
    $health = 'mirror_unwritable'
} elseif ($tunnelEnabled -and -not $localWsReady) {
    $health = 'gateway_tunnel_down'
} elseif (-not $gatewayOk) {
    $health = if ($statusUrl) { 'gateway_unreachable' } else { 'healthy' }
} elseif (-not $gatewayPayload.agent_connected) {
    $health = 'agent_disconnected'
} elseif ($gatewayPayload.worker_id -and $gatewayPayload.worker_id -ne $workerId) {
    $health = 'worker_mismatch'
} elseif ($gatewayPayload.primary_transport_mode -ne 'websocket_primary') {
    $health = 'websocket_not_primary'
} elseif (-not $gatewayPayload.websocket_log_mirror_enabled) {
    $health = 'websocket_log_mirror_disabled'
} elseif (-not $gatewayPayload.websocket_log_mirror_loop_bound) {
    $health = 'websocket_log_mirror_unbound'
}

Write-Host "health_category=$health"
Write-Host "task_state=$($task.State)"
Write-Host "last_run_time=$($taskInfo.LastRunTime)"
Write-Host "last_result=$($taskInfo.LastTaskResult)"
Write-Host "worker_id=$workerId"
Write-Host "log_dir=$logDir"
Write-Host "log_writable=$logWritable"
Write-Host "tunnel_enabled=$tunnelEnabled"
Write-Host "tunnel_local_ws_port=$localWsPort"
Write-Host "tunnel_local_ws_ready=$localWsReady"
Write-Host "status_url=$statusUrl"
if ($gatewayPayload) {
    Write-Host "agent_connected=$($gatewayPayload.agent_connected)"
    Write-Host "primary_transport_mode=$($gatewayPayload.primary_transport_mode)"
    Write-Host "websocket_health_ok=$($gatewayPayload.websocket_health_ok)"
    Write-Host "fallback_ready=$($gatewayPayload.fallback_ready)"
    Write-Host "websocket_log_mirror_enabled=$($gatewayPayload.websocket_log_mirror_enabled)"
    Write-Host "websocket_log_mirror_loop_bound=$($gatewayPayload.websocket_log_mirror_loop_bound)"
    Write-Host "websocket_log_mirror_last_ack_at=$($gatewayPayload.websocket_log_mirror_last_ack_at)"
    Write-Host "websocket_log_mirror_last_error=$($gatewayPayload.websocket_log_mirror_last_error)"
}

if ($health -ne 'healthy') { exit 1 }
