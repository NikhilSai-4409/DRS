param(
    [int]$ApiPort = 8765,
    [int]$FrontendPort = 5173,
    [switch]$SkipModelDownload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating Python virtual environment..."
    python -m venv (Join-Path $Root ".venv")
}

Write-Host "Installing Python dependencies..."
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

$bootstrapArgs = @("scripts\bootstrap_public_assets.py")
if ($SkipModelDownload) {
    $bootstrapArgs += "--skip-model"
}
& $Python @bootstrapArgs

Write-Host "Generating synchronized demo videos..."
& $Python "scripts\generate_test_video.py" --dual --output "data\testing\demo_delivery.mp4" --duration 6 --fps 60 --width 1280 --height 720

$TestingPlatform = Join-Path $Root "dashboard\testing-platform"
if (-not (Test-Path (Join-Path $TestingPlatform "node_modules"))) {
    Write-Host "Installing testing-platform dependencies..."
    Push-Location $TestingPlatform
    npm install
    Pop-Location
}

Write-Host "Starting FastAPI testing backend on http://127.0.0.1:$ApiPort"
Start-Process -FilePath $Python -ArgumentList @("drs_app.py", "--testing-api", "--host", "127.0.0.1", "--port", "$ApiPort") -WorkingDirectory $Root -WindowStyle Hidden

Write-Host "Starting React testing platform on http://127.0.0.1:$FrontendPort"
Start-Process -FilePath "npm" -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort") -WorkingDirectory $TestingPlatform -WindowStyle Hidden

Write-Host ""
Write-Host "Demo videos:"
Write-Host "  data\testing\demo_delivery_cam0.mp4"
Write-Host "  data\testing\demo_delivery_cam1.mp4"
Write-Host ""
Write-Host "Open: http://127.0.0.1:$FrontendPort"
Write-Host "Upload one video for single-camera mode or both videos for dual-camera mode."
