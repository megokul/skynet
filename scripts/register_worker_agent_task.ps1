param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$EnvFile = '.env.worker-agent',
    [string]$VenvPath = 'venv-agent',
    [string]$TaskName = 'OpenClawWorkerAgent',
    [switch]$StartNow = $true
)

$ErrorActionPreference = 'Stop'
Set-Location $RepoRoot

function Resolve-VenvPython {
    param(
        [string]$Root,
        [string]$RelativeVenv
    )
    $venvRoot = Join-Path $Root $RelativeVenv
    $candidates = @(
        (Join-Path $venvRoot 'Scripts\python.exe'),
        (Join-Path $venvRoot 'scripts\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }
    throw "Missing worker venv python under $venvRoot"
}

$pythonExe = Resolve-VenvPython -Root $RepoRoot -RelativeVenv $VenvPath
$runner = Join-Path $RepoRoot 'scripts\run_worker_agent.ps1'
if (-not (Test-Path $runner)) {
    throw "Missing worker runner script: $runner"
}

$argList = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $runner),
    '-RepoRoot', ('"{0}"' -f $RepoRoot),
    '-EnvFile', ('"{0}"' -f (Join-Path $RepoRoot $EnvFile)),
    '-PythonPath', ('"{0}"' -f $pythonExe)
) -join ' '

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argList
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
