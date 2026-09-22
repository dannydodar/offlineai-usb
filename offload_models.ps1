param(
    [ValidateSet('e2b','e4b','all')]
    [string]$Model = 'e2b',
    [string]$Destination = ''
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Join-Path $Root 'models'
if ([string]::IsNullOrWhiteSpace($Destination)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'LOCALAPPDATA is not available; cannot choose a PC cache location.' }
    $Destination = Join-Path $env:LOCALAPPDATA 'OfflineAI\models'
}

$sets = @{
    e2b = @{ folder = 'gemma-4-E2B-it-GGUF'; files = @('gemma-4-E2B-it-Q4_K_M.gguf', 'mmproj-gemma-4-E2B-it-BF16.gguf') }
    e4b = @{ folder = 'gemma-4-E4B-it-GGUF'; files = @('gemma-4-E4B-it-Q4_K_M.gguf', 'mmproj-gemma-4-E4B-it-BF16.gguf') }
}
$selected = if ($Model -eq 'all') { @('e2b', 'e4b') } else { @($Model) }
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
$driveName = ([System.IO.Path]::GetPathRoot($destinationRoot)).Substring(0, 1)
$destinationDrive = Get-PSDrive -Name $driveName -ErrorAction Stop
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null

foreach ($key in $selected) {
    $set = $sets[$key]
    $sourceFolder = Join-Path $sourceRoot $set.folder
    $destinationFolder = Join-Path $destinationRoot $set.folder
    New-Item -ItemType Directory -Force -Path $destinationFolder | Out-Null
    $manifestPath = Join-Path $destinationFolder '.offlineai-source.json'
    $manifest = @{}
    if (Test-Path -LiteralPath $manifestPath) {
        try {
            $oldManifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
            foreach ($property in $oldManifest.PSObject.Properties) { $manifest[$property.Name] = [string]$property.Value }
        } catch { $manifest = @{} }
    }

    $copyPlan = @()
    [long]$copyBytes = 0
    foreach ($fileName in $set.files) {
        $source = Join-Path $sourceFolder $fileName
        $destination = Join-Path $destinationFolder $fileName
        if (-not (Test-Path -LiteralPath $source)) { throw "Required model file not found: $source" }
        $sourceInfo = Get-Item -LiteralPath $source
        $stamp = "$($sourceInfo.Length)|$($sourceInfo.LastWriteTimeUtc.Ticks)"
        $destinationInfo = Get-Item -LiteralPath $destination -ErrorAction SilentlyContinue
        $needsCopy = (-not $destinationInfo) -or ($destinationInfo.Length -ne $sourceInfo.Length) -or ($manifest[$fileName] -ne $stamp)
        if ($needsCopy) {
            $copyPlan += [pscustomobject]@{ Source = $source; Destination = $destination; Stamp = $stamp; Length = [long]$sourceInfo.Length }
            $copyBytes += [long]$sourceInfo.Length
        }
    }

    if ($copyBytes -gt 0) {
        $reserveBytes = 512MB
        if ($destinationDrive.Free -lt ($copyBytes + $reserveBytes)) {
            throw "Not enough free space on $($destinationDrive.Name):. Need approximately $([math]::Round(($copyBytes + $reserveBytes) / 1GB, 2)) GB including reserve."
        }
        foreach ($item in $copyPlan) {
            Write-Output "Copying $([math]::Round($item.Length / 1GB, 2)) GB for $key to $($item.Destination)..."
            Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
            $copied = Get-Item -LiteralPath $item.Destination
            if ($copied.Length -ne $item.Length) { throw "Model copy verification failed: $($item.Destination)" }
            $manifest[(Split-Path -Leaf $item.Source)] = $item.Stamp
        }
        $manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
        Write-Output "Model $key is available in the PC cache: $destinationFolder"
    } else {
        Write-Output "Model $key is already current in the PC cache: $destinationFolder"
    }
}
