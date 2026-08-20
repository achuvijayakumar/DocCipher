# Build DocCipher Breaker: tests -> exe -> installer.
#
#   .\build.ps1              full build
#   .\build.ps1 -SkipTests   skip the test run
#   .\build.ps1 -NoInstaller stop after the exe

param(
    [switch]$SkipTests,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Green }

Step "Checking toolchain"
python --version

# Note: in Windows PowerShell 5.1, a native command writing anything to stderr
# raises NativeCommandError while $ErrorActionPreference is "Stop" -- even a
# harmless pip warning. Suppress it locally and judge by the exit code.
$hasPyInstaller = $false
try {
    $ErrorActionPreference = "Continue"
    python -m pip show pyinstaller 2>&1 | Out-Null
    $hasPyInstaller = ($LASTEXITCODE -eq 0)
} finally {
    $ErrorActionPreference = "Stop"
}

if (-not $hasPyInstaller) {
    Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
    python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Could not install PyInstaller." }
}

if (-not $SkipTests) {
    Step "Running tests"
    try {
        $ErrorActionPreference = "Continue"
        python -m pytest tests/ -q
    } finally {
        $ErrorActionPreference = "Stop"
    }
    if ($LASTEXITCODE -ne 0) { throw "Tests failed -- build aborted." }
}

Step "Generating branding assets"
try {
    $ErrorActionPreference = "Continue"
    python assets/build_assets.py
} finally {
    $ErrorActionPreference = "Stop"
}
if ($LASTEXITCODE -ne 0) { throw "Asset generation failed." }

Step "Cleaning previous build"
foreach ($dir in @("build", "dist", "dist_installer")) {
    if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
}

Step "Building executable"
try {
    $ErrorActionPreference = "Continue"
    python -m PyInstaller DocCipherBreaker.spec --noconfirm --clean
} finally {
    $ErrorActionPreference = "Stop"
}
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = "dist\DocCipherBreaker.exe"
if (-not (Test-Path $exe)) { throw "Expected $exe was not produced." }
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built $exe ($sizeMb MB)" -ForegroundColor Green

if ($NoInstaller) { Step "Done (installer skipped)"; exit 0 }

Step "Building installer"
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup 6 not found -- skipping installer." -ForegroundColor Yellow
    Write-Host "Install from https://jrsoftware.org/isdl.php, then re-run." -ForegroundColor Yellow
    exit 0
}

try {
    $ErrorActionPreference = "Continue"
    & $iscc "installer\setup.iss"
} finally {
    $ErrorActionPreference = "Stop"
}
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

Step "Done"
Get-ChildItem dist_installer | Format-Table Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}
