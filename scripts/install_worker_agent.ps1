param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$EnvFile = '.env.worker-agent',
    [string]$VenvPath = 'venv-agent',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

$envTarget = Join-Path $RepoRoot $EnvFile
$envExample = Join-Path $RepoRoot '.env.worker-agent.example'
if ((-not (Test-Path $envTarget)) -and (Test-Path $envExample)) {
    Copy-Item $envExample $envTarget
}
if (-not (Test-Path $envTarget)) {
    throw "Missing worker env file: $envTarget"
}

$venvFull = Join-Path $RepoRoot $VenvPath
if ($Force -and (Test-Path $venvFull)) {
    Remove-Item $venvFull -Recurse -Force
}
if (-not (Test-Path (Join-Path $venvFull 'Scripts\python.exe'))) {
    python -m venv $venvFull
}

$pythonExe = Join-Path $venvFull 'Scripts\python.exe'
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $RepoRoot 'openclaw-agent\requirements.txt')

Write-Host "worker_agent_env=$envTarget"
Write-Host "worker_agent_python=$pythonExe"
