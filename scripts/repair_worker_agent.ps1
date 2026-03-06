param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$EnvFile = '.env.worker-agent',
    [string]$VenvPath = 'venv-agent',
    [string]$TaskName = 'OpenClawWorkerAgent'
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'scripts\install_worker_agent.ps1') -RepoRoot $RepoRoot -EnvFile $EnvFile -VenvPath $VenvPath
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'scripts\register_worker_agent_task.ps1') -RepoRoot $RepoRoot -EnvFile $EnvFile -VenvPath $VenvPath -TaskName $TaskName -StartNow
Start-Sleep -Seconds 5
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot 'scripts\check_worker_agent_health.ps1') -RepoRoot $RepoRoot -EnvFile $EnvFile -TaskName $TaskName
