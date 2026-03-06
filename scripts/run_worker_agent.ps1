param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$EnvFile = '.env.worker-agent',
    [string]$PythonPath = '',
    [string]$BootstrapLog = ''
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

if (-not $BootstrapLog) {
    $BootstrapLog = Join-Path $RepoRoot 'logs\worker-agent.bootstrap.log'
}
New-Item -ItemType Directory -Force -Path (Split-Path $BootstrapLog -Parent) | Out-Null
$transcriptStarted = $false
try {
    Start-Transcript -Path $BootstrapLog -Force | Out-Null
    $transcriptStarted = $true
} catch {
    Write-Warning "Worker-agent transcript could not start: $($_.Exception.Message)"
}

function Import-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { throw "Env file not found: $Path" }
    foreach ($rawLine in Get-Content $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
        $parts = $line.Split('=', 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], 'Process')
    }
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

Import-EnvFile -Path $EnvFile
Write-Host "worker_agent.env_file=$EnvFile"
Write-Host "worker_agent.gateway_url=$($env:SKYNET_GATEWAY_URL)"
Write-Host "worker_agent.auth_token_present=$([bool]$env:SKYNET_AUTH_TOKEN)"

$tunnelProcess = $null
    $tunnelEnabledRaw = if ($env:SKYNET_AGENT_TUNNEL_ENABLED) { $env:SKYNET_AGENT_TUNNEL_ENABLED } else { '' }
    $tunnelEnabled = @('1','true','yes','on') -contains ($tunnelEnabledRaw.ToLowerInvariant())
Write-Host "worker_agent.tunnel_enabled=$tunnelEnabled"
if ($tunnelEnabled) {
    $tunnelHost = if ($env:SKYNET_AGENT_TUNNEL_HOST) { $env:SKYNET_AGENT_TUNNEL_HOST } elseif ($env:OPENCLAW_TUNNEL_EC2_HOST) { $env:OPENCLAW_TUNNEL_EC2_HOST } else { '' }
    $tunnelUser = if ($env:SKYNET_AGENT_TUNNEL_USER) { $env:SKYNET_AGENT_TUNNEL_USER } elseif ($env:OPENCLAW_TUNNEL_EC2_USER) { $env:OPENCLAW_TUNNEL_EC2_USER } else { '' }
    $tunnelKey = if ($env:SKYNET_AGENT_TUNNEL_KEY) { $env:SKYNET_AGENT_TUNNEL_KEY } elseif ($env:OPENCLAW_TUNNEL_SSH_KEY) { $env:OPENCLAW_TUNNEL_SSH_KEY } elseif ($env:OPENCLAW_SSH_KEY_PATH) { $env:OPENCLAW_SSH_KEY_PATH } else { '' }
    $localWsPort = 0
    $localWsPortRaw = if ($env:SKYNET_AGENT_TUNNEL_LOCAL_WS_PORT) { $env:SKYNET_AGENT_TUNNEL_LOCAL_WS_PORT } else { '18765' }
    [void][int]::TryParse($localWsPortRaw, [ref]$localWsPort)
    if ($localWsPort -le 0) { $localWsPort = 18765 }
    $remoteWsHost = if ($env:SKYNET_AGENT_TUNNEL_REMOTE_WS_HOST) { $env:SKYNET_AGENT_TUNNEL_REMOTE_WS_HOST } else { '127.0.0.1' }
    $remoteWsPort = 0
    $remoteWsPortRaw = if ($env:SKYNET_AGENT_TUNNEL_REMOTE_WS_PORT) { $env:SKYNET_AGENT_TUNNEL_REMOTE_WS_PORT } else { '8765' }
    [void][int]::TryParse($remoteWsPortRaw, [ref]$remoteWsPort)
    if ($remoteWsPort -le 0) { $remoteWsPort = 8765 }

    if (-not $tunnelHost -or -not $tunnelUser -or -not $tunnelKey) {
        throw "SKYNET_AGENT_TUNNEL_ENABLED=1 requires tunnel host/user/key."
    }
    if (-not (Test-Path $tunnelKey)) {
        throw "Tunnel SSH key not found: $tunnelKey"
    }
    if (-not $env:SKYNET_GATEWAY_URL) {
        $env:SKYNET_GATEWAY_URL = "ws://127.0.0.1:$localWsPort/agent/ws"
    }
    Write-Host "worker_agent.tunnel_target=${tunnelUser}@${tunnelHost} 127.0.0.1:${localWsPort}->${remoteWsHost}:${remoteWsPort}"

    if (-not (Test-TcpPort -TargetHost '127.0.0.1' -Port $localWsPort -TimeoutMs 800)) {
        $argList = @(
            '-i', $tunnelKey,
            '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ServerAliveInterval=30',
            '-o', 'ServerAliveCountMax=3',
            '-N',
            '-L', "127.0.0.1:${localWsPort}:${remoteWsHost}:${remoteWsPort}",
            "${tunnelUser}@${tunnelHost}"
        )
        $tunnelProcess = Start-Process ssh -ArgumentList $argList -WindowStyle Hidden -PassThru
        Write-Host "worker_agent.tunnel_pid=$($tunnelProcess.Id)"
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline) {
            if (Test-TcpPort -TargetHost '127.0.0.1' -Port $localWsPort -TimeoutMs 800) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-TcpPort -TargetHost '127.0.0.1' -Port $localWsPort -TimeoutMs 800)) {
            if ($tunnelProcess -and -not $tunnelProcess.HasExited) {
                Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
            }
            throw "Worker-agent websocket tunnel failed to open on localhost:$localWsPort"
        }
    }
    Write-Host "worker_agent.tunnel_ready=True"
}

if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $RepoRoot 'venv-agent\Scripts\python.exe'),
        (Join-Path $RepoRoot 'venv\Scripts\python.exe'),
        'python'
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq 'python' -or (Test-Path $candidate)) {
            $PythonPath = $candidate
            break
        }
    }
}

try {
    Write-Host "worker_agent.python=$PythonPath"
    & $PythonPath (Join-Path $RepoRoot 'openclaw-agent\main.py')
    exit $LASTEXITCODE
} finally {
    if ($tunnelProcess -and -not $tunnelProcess.HasExited) {
        Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
}
