param(
    [int]$Port = 8000,
    [switch]$InstallDependencies,
    [switch]$Reload
)
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$HostAddress = "127.0.0.1"
$Url = "http://${HostAddress}:${Port}/"
$LocalUv = Join-Path $ProjectRoot ".tools\uv\uv.exe"
$UvCommand = $null

if (Test-Path -LiteralPath $LocalUv) {
    $UvCommand = $LocalUv
} else {
    $FoundUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($FoundUv) {
        $UvCommand = $FoundUv.Source
    }
}

Write-Host "Starting PV MVP from: $ProjectRoot"
Write-Host "Checking port $Port..."

$Listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Listeners) {
    throw "Port $Port is busy. Stop your PV1 server explicitly or use start.ps1 -Port 8001."
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = $VenvPython

if (-not (Test-Path -LiteralPath $Python)) {
    if (-not $InstallDependencies) {
        throw "Run start.ps1 -InstallDependencies to set up the environment, then initialize the database as described in README."
    }
    Write-Host "Virtual environment was not found. Creating .venv..."

    if ($UvCommand) {
        & $UvCommand venv ".venv" --python 3.12
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create .venv with uv."
        }
    } else {
        $SystemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $SystemPython) {
            throw "Python was not found. Install Python 3.11+ or create .venv before running start.ps1."
        }

        & $SystemPython.Source -m venv ".venv"
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create .venv."
        }
    }

    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Virtual environment was created, but .venv\Scripts\python.exe was not found."
    }
}

if ($InstallDependencies) {
Write-Host "Installing dependencies from requirements.lock.txt..."
if ($UvCommand) {
    & $UvCommand pip install -r "requirements.lock.txt" --python $Python
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with uv."
    }
} else {
    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip was not found in .venv. Bootstrapping pip..."
        & $Python -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to bootstrap pip."
        }
    }

    & $Python -m pip install -r "requirements.lock.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}
}

Write-Host "Opening site at $Url"
Write-Host "Press Ctrl+C to stop the server."

$ServerArguments = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", $Port)
if ($Reload) { $ServerArguments += "--reload" }
& $Python @ServerArguments
