$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$HostAddress = "127.0.0.1"
$Port = 8000
$Url = "http://${HostAddress}:${Port}/"
$CurrentProcessId = [System.Diagnostics.Process]::GetCurrentProcess().Id
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
$ProcessIds = $Listeners | Select-Object -ExpandProperty OwningProcess -Unique

foreach ($ProcessId in $ProcessIds) {
    if ($ProcessId -and $ProcessId -ne $CurrentProcessId) {
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($Process) {
            Write-Host "Port $Port is busy. Stopping process $($Process.Id) ($($Process.ProcessName))..."
            Stop-Process -Id $Process.Id -Force
        }
    }
}

Start-Sleep -Milliseconds 500

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = $VenvPython

if (-not (Test-Path -LiteralPath $Python)) {
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

Write-Host "Installing/updating dependencies from requirements.txt..."
if ($UvCommand) {
    & $UvCommand pip install -r "requirements.txt" --python $Python
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

    & $Python -m pip install -r "requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}

Write-Host "Opening site at $Url"
Write-Host "Press Ctrl+C to stop the server."

& $Python -m uvicorn app.main:app --host $HostAddress --port $Port --reload
