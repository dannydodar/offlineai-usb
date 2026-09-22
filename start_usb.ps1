param(
    [ValidateSet('auto','e2b','e4b','qwen3')]
    [string]$Model = 'auto',
    [int]$Port = 8765,
    [switch]$NoOffload
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$driveRoot = Split-Path -Qualifier $Root
$Python = Join-Path $Root 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw "Portable Python runtime not found: $Python" }
$runner = Join-Path $Root 'runtime\llama-cpp-cpu-b11102\llama-server.exe'
if (-not (Test-Path -LiteralPath $runner)) { throw "Portable llama.cpp runner not found: $runner" }
$totalMemoryBytes = 0
$logicalProcessors = [Environment]::ProcessorCount
try {
    $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $totalMemoryBytes = [long]$computer.TotalPhysicalMemory
    if ($computer.NumberOfLogicalProcessors) { $logicalProcessors = [int]$computer.NumberOfLogicalProcessors }
} catch { }
$lowResource = (($totalMemoryBytes -gt 0) -and ($totalMemoryBytes -le 6GB)) -or (($logicalProcessors -le 4) -and ($totalMemoryBytes -gt 0) -and ($totalMemoryBytes -le 8GB))
if ($Model -eq 'auto') {
    $qwenCandidate = Join-Path (Join-Path $Root 'models') 'qwen3-0.6b\Qwen3-0.6B-Q4_0.gguf'
    $Model = if ($lowResource -and (Test-Path -LiteralPath $qwenCandidate)) { 'qwen3' } else { 'e2b' }
}
$modelName = switch ($Model) {
    'e4b' { 'gemma-4-E4B-it-GGUF\gemma-4-E4B-it-Q4_K_M.gguf' }
    'qwen3' { 'qwen3-0.6b\Qwen3-0.6B-Q4_0.gguf' }
    default { 'gemma-4-E2B-it-GGUF\gemma-4-E2B-it-Q4_K_M.gguf' }
}
$modelAlias = switch ($Model) {
    'e4b' { 'google/gemma-4-e4b' }
    'qwen3' { 'qwen/qwen3-0.6b' }
    default { 'google/gemma-4-e2b' }
}
$modelPath = Join-Path (Join-Path $Root 'models') $modelName
if (-not (Test-Path -LiteralPath $modelPath)) { throw "Selected model not found: $modelPath" }
$modelFolderForBackend = Join-Path $Root 'models'
$modelLocation = 'USB'
if (-not $NoOffload) {
    try {
        & (Join-Path $Root 'offload_models.ps1') -Model $Model
        $pcModelRoot = Join-Path $env:LOCALAPPDATA 'OfflineAI\models'
        $pcModelPath = Join-Path $pcModelRoot $modelName
        if (Test-Path -LiteralPath $pcModelPath) {
            $modelPath = $pcModelPath
            $modelFolderForBackend = $pcModelRoot
            $modelLocation = 'PC cache'
        } else {
            Write-Warning "Model offload did not produce the expected local file; using the USB copy."
        }
    } catch {
        Write-Warning "Model offload unavailable ($($_.Exception.Message)); using the USB copy."
    }
}
$pdfRoot = Join-Path $driveRoot 'PDF'
if (-not (Test-Path -LiteralPath $pdfRoot)) { throw "PDF library not found at $pdfRoot" }
$logDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runnerPidFile = Join-Path $logDir 'usb-runner.pid'
$backendPidFile = Join-Path $logDir 'usb-backend.pid'
$runnerPort = 1235
$hostAddress = '127.0.0.1'

function Test-PortAvailable([int]$Candidate) {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Candidate -ErrorAction SilentlyContinue
    if ($listeners) { return $false }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Candidate)
    try {
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

foreach ($pidFile in $runnerPidFile,$backendPidFile) {
    if (Test-Path -LiteralPath $pidFile) {
        Stop-Process -Id ([int](Get-Content -Raw -LiteralPath $pidFile)) -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
}

$requestedPort = $Port
if (-not (Test-PortAvailable $Port)) {
    $fallbackPort = 8775..8790 | Where-Object { Test-PortAvailable $_ } | Select-Object -First 1
    if (-not $fallbackPort) {
        throw "OfflineAI web port $requestedPort is busy and no fallback port from 8775 to 8790 is available."
    }
    $Port = [int]$fallbackPort
    Write-Output "Port $requestedPort is already in use; using free fallback port $Port."
}

$contextSize = if ($lowResource) { 2048 } else { 4096 }
$runnerProcess = Start-Process -FilePath $runner -ArgumentList @('--model', $modelPath, '--alias', $modelAlias, '--host', '127.0.0.1', '--port', $runnerPort, '--ctx-size', [string]$contextSize, '--n-gpu-layers', '0', '--parallel', '1') -WorkingDirectory (Split-Path $runner -Parent) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'usb-runner.out.log') -RedirectStandardError (Join-Path $logDir 'usb-runner.err.log')
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
$env:OFFLINEAI_ENGINE = "llama.cpp CPU (independent, $Model; model on $modelLocation)"
$env:OFFLINEAI_ACTIVE_MODEL = $modelAlias
$env:OFFLINEAI_MODEL_FOLDER = $modelFolderForBackend
$backend = Start-Process -FilePath $Python -ArgumentList @('-u', (Join-Path $Root 'app\server.py')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'usb-backend.out.log') -RedirectStandardError (Join-Path $logDir 'usb-backend.err.log')
Set-Content -LiteralPath $backendPidFile -Value $backend.Id -Encoding ascii
Start-Sleep -Milliseconds 800
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/health" -TimeoutSec 5 | Out-Null } catch { throw 'Portable OfflineAI backend did not become healthy.' }
Start-Process "http://127.0.0.1:$Port/"
Write-Output "Portable OfflineAI started at http://127.0.0.1:$Port/ using $Model through independent llama.cpp; model source: $modelLocation."
