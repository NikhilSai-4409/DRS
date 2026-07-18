param(
    [string]$HostAddress = "127.0.0.1",
    [int]$BackendPort = 8765,
    [int]$FrontendPort = 5174
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Frontend = Join-Path $Root "dashboard\testing-platform"

if (!(Test-Path $Python)) {
    throw "Python virtual environment not found at $Python"
}

Start-Process -FilePath $Python -ArgumentList @(
    "drs_app.py",
    "--testing-api",
    "--host",
    $HostAddress,
    "--port",
    "$BackendPort"
) -WorkingDirectory $Root -WindowStyle Minimized

Start-Sleep -Seconds 3

Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--host", $HostAddress, "--port", "$FrontendPort") -WorkingDirectory $Frontend -WindowStyle Minimized

Start-Sleep -Seconds 2
Start-Process "http://$HostAddress`:$FrontendPort"
