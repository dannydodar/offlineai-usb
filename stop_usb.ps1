param([switch]$Quiet)
$ErrorActionPreference = 'SilentlyContinue'
$Root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path.TrimEnd('\')
$rootPattern = [regex]::Escape($Root)

function Write-StopMessage([string]$Message) {
    if (-not $Quiet) { Write-Output $Message }
}

function Stop-OfflineProcess([int]$ProcessId, [string]$Reason) {
    if ($ProcessId -le 0) { return }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if (-not $process) { return }
    $commandLine = [string]$process.CommandLine
    $isBackend = $commandLine -match 'app[\\/]server\.py'
    $isRunner = $commandLine -match 'llama-server\.exe'
    if ($commandLine -notmatch $rootPattern -or (-not $isBackend -and -not $isRunner)) { return }
    Stop-Process -Id $ProcessId -Force
    Start-Sleep -Milliseconds 250
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        Write-StopMessage ('Stopped portable OfflineAI process PID ' + $ProcessId + ' (' + $Reason + ').')
    }
}

$logRoot = Join-Path $Root 'logs'
foreach ($file in 'usb-backend.pid','usb-runner.pid') {
    $path = Join-Path $logRoot $file
    if (Test-Path -LiteralPath $path) {
        try { $pidValue = [int](Get-Content -Raw -LiteralPath $path); Stop-OfflineProcess $pidValue 'tracked process' } catch { }
        Remove-Item -LiteralPath $path -Force
    }
}

# Clean up orphaned OfflineAI processes if a PID file was deleted or a
# previous launcher was interrupted. The root/command-line checks prevent
# this from touching unrelated Python or llama.cpp processes.
try {
    $orphaned = Get-CimInstance Win32_Process | Where-Object {
        $commandLine = [string]$_.CommandLine
        $commandLine -match $rootPattern -and
        (($commandLine -match 'app[\\/]server\.py') -or ($commandLine -match 'llama-server\.exe'))
    }
    foreach ($process in $orphaned) { Stop-OfflineProcess ([int]$process.ProcessId) 'orphan cleanup' }
} catch { }
