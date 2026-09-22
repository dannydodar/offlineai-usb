$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($file in 'gateway-backend.pid','independent-runner.pid') {
    $path = Join-Path $Root ('logs\' + $file)
    if (Test-Path -LiteralPath $path) {
        $pidValue = [int](Get-Content -Raw -LiteralPath $path)
        Stop-Process -Id $pidValue -Force
        Remove-Item -LiteralPath $path -Force
        Write-Output ('Stopped process PID ' + $pidValue + ' from ' + $file)
    }
}
