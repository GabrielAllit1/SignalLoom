$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "== SignalLoomOps Windows Build ==" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required for the build machine. Activate a Python 3.11+ environment, then rerun this script."
}

$PyVersion = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ([version]$PyVersion -lt [version]"3.11") {
    throw "Python 3.11+ is required. Current Python is $PyVersion. Activate conda environment signalloom or install Python 3.11+."
}
Write-Host "Build Python: $((Get-Command python).Source) ($PyVersion)" -ForegroundColor Gray

$RecreateVenv = $false
if (Test-Path ".venv\Scripts\python.exe") {
    $VenvVersion = (& .\.venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
    if ($VenvVersion -ne $PyVersion) {
        Write-Host "Existing .venv uses Python $VenvVersion; recreating with Python $PyVersion..." -ForegroundColor Yellow
        $RecreateVenv = $true
    }
} else {
    $RecreateVenv = $true
}

if ($RecreateVenv) {
    if (Test-Path ".venv") { Remove-Item ".venv" -Recurse -Force }
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
& .\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm SignalLoomOps.spec

function Find-InnoCompiler {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($item in $candidates) {
        if ($item -and (Test-Path $item)) { return $item }
    }
    return $null
}

$Iscc = Find-InnoCompiler
if (-not $Iscc) {
    $LocalInstaller = Join-Path $Root "installer\innosetup-6.7.3.exe"
    if (Test-Path $LocalInstaller) {
        Write-Host "Local Inno Setup installer found. Installing silently..." -ForegroundColor Yellow
        Start-Process -FilePath $LocalInstaller -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" -Wait
        Start-Sleep -Seconds 2
        $Iscc = Find-InnoCompiler
    }
}

if (-not $Iscc) {
    Write-Host "PyInstaller app built at dist\SignalLoomOps." -ForegroundColor Yellow
    Write-Host "Install Inno Setup 6, then rerun this script to create dist\installer\SignalLoomOps_Setup.exe." -ForegroundColor Yellow
    Write-Host "Inno Setup: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    exit 0
}

Write-Host "Using Inno compiler: $Iscc" -ForegroundColor Gray
& $Iscc "installer\SignalLoomOps.iss"
Write-Host "Done: dist\installer\SignalLoomOps_Setup.exe" -ForegroundColor Green
