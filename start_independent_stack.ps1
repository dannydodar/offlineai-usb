$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $Root 'runtime\llama-cpp-cpu-b11102\llama-server.exe'
$Model = 'H:\helper\models\lmstudio-community\gemma-4-E2B-it-GGUF\gemma-4-E2B-it-Q4_K_M.gguf'
$PythonCandidates = @(
    (Join-Path $Root 'runtime\python.exe'),
    'C:\Users\oogly\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
)
$Python = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $Runner)) { throw "Independent runner not found: $Runner" }
if (-not (Test-Path -LiteralPath $Model)) { throw "E2B model not found: $Model" }

$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runnerPidFile = Join-Path $logDir 'independent-runner.pid'
$backendPidFile = Join-Path $logDir 'gateway-backend.pid'
$runnerPort = 1235

if (Test-Path -LiteralPath $backendPidFile) {
    $oldBackendPid = [int](Get-Content -Raw -LiteralPath $backendPidFile)
    Stop-Process -Id $oldBackendPid -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backendPidFile -Force -ErrorAction SilentlyContinue
}

try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$runnerPort/health" -TimeoutSec 2 | Out-Null }
catch {
    $runner = Start-Process -FilePath $Runner -ArgumentList @('--model', $Model, '--alias', 'google/gemma-4-e2b', '--host', '127.0.0.1', '--port', $runnerPort, '--ctx-size', '4096', '--n-gpu-layers', '0', '--parallel', '1') -WorkingDirectory (Split-Path $Runner -Parent) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'independent-runner.out.log') -RedirectStandardError (Join-Path $logDir 'independent-runner.err.log')
    Set-Content -LiteralPath $runnerPidFile -Value $runner.Id -Encoding ascii
    $ready = $false
    for ($i=0; $i -lt 120; $i++) {
        try { $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$runnerPort/health" -TimeoutSec 2; if ($health.StatusCode -eq 200) { $ready = $true; break } } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Independent llama.cpp runner did not become healthy within 120 seconds.' }
}

$env:OFFLINEAI_HOST = '127.0.0.1'
$env:OFFLINEAI_PORT = '8766'
$env:OFFLINEAI_DB_PATH = Join-Path $Root 'worker-pdf\pdf_catalog.sqlite3'
$env:OFFLINEAI_PDF_ROOT = 'E:\PDF'
$env:OFFLINEAI_LM_BASE = "http://127.0.0.1:$runnerPort/v1"
$env:OFFLINEAI_ENGINE = 'llama.cpp CPU (independent)'
$env:OFFLINEAI_MODEL_FOLDER = 'H:\helper\models\lmstudio-community'
$backend = Start-Process -FilePath $Python -ArgumentList @('-u', (Join-Path $Root 'app\server.py')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'gateway-backend.out.log') -RedirectStandardError (Join-Path $logDir 'gateway-backend.err.log')
Set-Content -LiteralPath $backendPidFile -Value $backend.Id -Encoding ascii
Start-Sleep -Milliseconds 700
try { Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8766/api/health' -TimeoutSec 5 | Out-Null } catch { throw 'OfflineAI backend did not become healthy against the independent runner.' }
Write-Output "Independent OfflineAI stack is ready: llama.cpp CPU on 127.0.0.1:$runnerPort, backend on 127.0.0.1:8766."
