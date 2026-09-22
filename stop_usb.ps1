$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($file in 'usb-backend.pid','usb-runner.pid') {
    $path = Join-Path $Root ('logs\' + $file)
    if (Test-Path -LiteralPath $path) {
        $pidValue = [int](Get-Content -Raw -LiteralPath $path)
        Stop-Process -Id $pidValue -Force
        Remove-Item -LiteralPath $path -Force
        Write-Output ('Stopped portable OfflineAI process PID ' + $pidValue)
    }
}
