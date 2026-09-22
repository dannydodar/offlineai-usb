param(
    [switch]$Lan,
    [ValidateSet('e2b','e4b')]
    [string]$Model = 'e2b',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$driveRoot = Split-Path -Qualifier $Root
$Python = Join-Path $Root 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw "Portable Python runtime not found: $Python" }
$runner = Join-Path $Root 'runtime\llama-cpp-cpu-b11102\llama-server.exe'
if (-not (Test-Path -LiteralPath $runner)) { throw "Portable llama.cpp runner not found: $runner" }
$modelName = if ($Model -eq 'e4b') { 'gemma-4-E4B-it-GGUF\gemma-4-E4B-it-Q4_K_M.gguf' } else { 'gemma-4-E2B-it-GGUF\gemma-4-E2B-it-Q4_K_M.gguf' }
$modelPath = Join-Path (Join-Path $Root 'models') $modelName
if (-not (Test-Path -LiteralPath $modelPath)) { throw "Selected model not found: $modelPath" }
$pdfRoot = Join-Path $driveRoot 'PDF'
if (-not (Test-Path -LiteralPath $pdfRoot)) { throw "PDF library not found at $pdfRoot" }
$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runnerPidFile = Join-Path $logDir 'usb-runner.pid'
$backendPidFile = Join-Path $logDir 'usb-backend.pid'
$runnerPort = 1235
$hostAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }

foreach ($pidFile in $runnerPidFile,$backendPidFile) {
    if (Test-Path -LiteralPath $pidFile) {
        Stop-Process -Id ([int](Get-Content -Raw -LiteralPath $pidFile)) -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

$runnerProcess = Start-Process -FilePath $runner -ArgumentList @('--model', $modelPath, '--alias', ("google/gemma-4-$Model"), '--host', '127.0.0.1', '--port', $runnerPort, '--ctx-size', '4096', '--n-gpu-layers', '0', '--parallel', '1') -WorkingDirectory (Split-Path $runner -Parent) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'usb-runner.out.log') -RedirectStandardError (Join-Path $logDir 'usb-runner.err.log')
Set-Content -LiteralPath $runnerPidFile -Value $runnerProcess.Id -Encoding ascii
$ready = $false
for ($i=0; $i -lt 180; $i++) {
    try { $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$runnerPort/health" -TimeoutSec 2; if ($health.StatusCode -eq 200) { $ready = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Portable llama.cpp runner did not become healthy within 180 seconds.' }

$env:OFFLINEAI_HOST = $hostAddress
$env:OFFLINEAI_PORT = [string]$Port
$env:OFFLINEAI_DB_PATH = Join-Path $Root 'worker-pdf\pdf_catalog.sqlite3'
$env:OFFLINEAI_PDF_ROOT = $pdfRoot
$env:OFFLINEAI_LM_BASE = "http://127.0.0.1:$runnerPort/v1"
$env:OFFLINEAI_ENGINE = "llama.cpp CPU (independent, $Model)"
$env:OFFLINEAI_MODEL_FOLDER = Join-Path $Root 'models'
$backend = Start-Process -FilePath $Python -ArgumentList @('-u', (Join-Path $Root 'app\server.py')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'usb-backend.out.log') -RedirectStandardError (Join-Path $logDir 'usb-backend.err.log')
Set-Content -LiteralPath $backendPidFile -Value $backend.Id -Encoding ascii
Start-Sleep -Milliseconds 800
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 5 | Out-Null } catch { throw 'Portable OfflineAI backend did not become healthy.' }
Start-Process "http://127.0.0.1:$Port/"
Write-Output "Portable OfflineAI started at http://127.0.0.1:$Port/ using $Model through independent llama.cpp."
