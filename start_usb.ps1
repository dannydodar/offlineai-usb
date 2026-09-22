param(
    [ValidateSet('auto','e2b','e4b','qwen3')]
    [string]$Model = 'qwen3',
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
    $qwenUsbCandidate = Join-Path (Join-Path $Root 'models') 'qwen3-0.6b\Qwen3-0.6B-Q4_0.gguf'
    $qwenPcCandidate = Join-Path (Join-Path $env:LOCALAPPDATA 'OfflineAI\models') 'qwen3-0.6b\Qwen3-0.6B-Q4_0.gguf'
    $hasQwen = (Test-Path -LiteralPath $qwenUsbCandidate) -or (Test-Path -LiteralPath $qwenPcCandidate)
    if (-not $hasQwen) {
        throw 'Lightweight-only mode is enabled, but Qwen3 0.6B is not installed. Use the model download option or explicitly start with -Model e2b temporarily.'
    }
    $Model = 'qwen3'
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
$usbModelPath = Join-Path (Join-Path $Root 'models') $modelName
$pcModelRoot = Join-Path $env:LOCALAPPDATA 'OfflineAI\models'
$pcModelPath = Join-Path $pcModelRoot $modelName
$modelPath = $usbModelPath
$modelFolderForBackend = Join-Path $Root 'models'
$modelLocation = 'USB'
if ((-not (Test-Path -LiteralPath $usbModelPath)) -and (Test-Path -LiteralPath $pcModelPath)) {
    $modelPath = $pcModelPath
    $modelFolderForBackend = $pcModelRoot
    $modelLocation = 'PC cache'
}
if (-not (Test-Path -LiteralPath $modelPath)) {
    if ($Model -eq 'qwen3') {
        throw "Lightweight mode requires Qwen3 0.6B, but it is not installed. Use the model download option or explicitly start with -Model e2b temporarily."
    }
    throw "Selected model not found on the USB or PC cache: $modelName"
}
if ((-not $NoOffload) -and ($modelLocation -ne 'PC cache')) {
    try {
        & (Join-Path $Root 'offload_models.ps1') -Model $Model
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

# Stop the old backend and llama.cpp process before checking ports or starting
# replacements. This also cleans orphaned processes left by an interrupted
# restart, while only matching processes owned by this OfflineAI folder.
$stopScript = Join-Path $Root 'stop_usb.ps1'
if (Test-Path -LiteralPath $stopScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Quiet
}

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
if (-not $ready) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Quiet
    throw 'Portable llama.cpp runner did not become healthy within 180 seconds.'
}
try {
    $runnerModels = Invoke-RestMethod -Uri "http://127.0.0.1:$runnerPort/v1/models" -TimeoutSec 3
    $runnerIds = @($runnerModels.data | ForEach-Object { [string]$_.id })
    if ($runnerIds.Count -gt 0 -and $runnerIds -notcontains $modelAlias) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Quiet
        throw "Portable runner loaded an unexpected model: $($runnerIds -join ', ')"
    }
} catch {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Quiet
    throw "Portable llama.cpp model check failed: $($_.Exception.Message)"
}

$env:OFFLINEAI_HOST = $hostAddress
$env:OFFLINEAI_PORT = [string]$Port
$env:OFFLINEAI_DB_PATH = Join-Path $Root 'worker-pdf\pdf_catalog.sqlite3'
$env:OFFLINEAI_PDF_ROOT = $pdfRoot
$env:OFFLINEAI_LM_BASE = "http://127.0.0.1:$runnerPort/v1"
$env:OFFLINEAI_ENGINE = "llama.cpp CPU (independent, $Model; model on $modelLocation)"
$env:OFFLINEAI_ACTIVE_MODEL = $modelAlias
$env:OFFLINEAI_MODEL_POLICY = if ($Model -eq 'qwen3') { 'lightweight' } else { 'normal' }
$env:OFFLINEAI_MODEL_FOLDER = $modelFolderForBackend
$backend = Start-Process -FilePath $Python -ArgumentList @('-u', (Join-Path $Root 'app\server.py')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'usb-backend.out.log') -RedirectStandardError (Join-Path $logDir 'usb-backend.err.log')
Set-Content -LiteralPath $backendPidFile -Value $backend.Id -Encoding ascii
$backendReady = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if ($health.ok -and $health.active_model -eq $modelAlias) { $backendReady = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $backendReady) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -Quiet
    throw 'Portable OfflineAI backend did not become healthy with the selected model.'
}
Start-Process "http://127.0.0.1:$Port/"
Write-Output "Portable OfflineAI started at http://127.0.0.1:$Port/ using $Model through independent llama.cpp; model source: $modelLocation."
