param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$RuntimeRoot = Join-Path $ProjectRoot '.tools\pv1-main'
$Python = if ($env:PV1_PYTHON) { $env:PV1_PYTHON } else { Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
$ProcessInspector = Join-Path $ProjectRoot 'tools\site_process.py'
$Database = Join-Path $ProjectRoot 'data\pv1.db'
$Storage = Join-Path $ProjectRoot 'data\storage'
$Url = "http://127.0.0.1:${Port}/"
$Mutex = $null
$OwnsMutex = $false

function Get-LocalPage([string]$Path) {
    $Response = $null
    $Reader = $null
    try {
        $Request = [System.Net.HttpWebRequest]::Create($Url + $Path)
        $Request.Proxy = $null
        $Request.Timeout = 1500
        $Request.ReadWriteTimeout = 1500
        $Response = $Request.GetResponse()
        $Reader = [System.IO.StreamReader]::new($Response.GetResponseStream())
        return $Reader.ReadToEnd()
    } catch {
        return $null
    } finally {
        if ($Reader) { $Reader.Dispose() }
        if ($Response) { $Response.Dispose() }
    }
}

function Test-SiteReady {
    $Health = Get-LocalPage 'health'
    if (-not $Health) { return $false }
    try {
        if (($Health | ConvertFrom-Json).status -ne 'ok') { return $false }
    } catch { return $false }
    $LoginPage = Get-LocalPage 'login'
    return ($LoginPage -and $LoginPage.Contains('class="auth-page"') -and $LoginPage.Contains('name="password"'))
}

function Test-PortInUse {
    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $Attempt = $Client.ConnectAsync('127.0.0.1', $Port)
        return ($Attempt.Wait(1500) -and $Client.Connected)
    } catch { return $false }
    finally { $Client.Dispose() }
}

function Get-SiteListeners {
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $Port })
}

function Confirm-SiteProcess {
    $Listeners = @(Get-SiteListeners)
    if ($Listeners.Count -eq 0) { throw "No listener found on port $Port." }
    if (@($Listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
        throw "Port $Port has a non-loopback listener. No process was stopped."
    }
    $ProcessIds = @($Listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($ProcessIds.Count -ne 1) { throw "Port $Port has multiple listeners. No process was stopped." }
    $SiteProcess = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $ProcessIds[0]) -ErrorAction Stop
    $ExpectedCommand = '-m uvicorn app.main:app --host 127.0.0.1 --port ' + $Port
    if (-not $SiteProcess -or $SiteProcess.Name -notmatch '^python(w)?\.exe$' -or
        -not $SiteProcess.CommandLine.TrimEnd().EndsWith($ExpectedCommand, [System.StringComparison]::Ordinal)) {
        throw "Port $Port is used by another service. No process was stopped."
    }
    $Inspection = & $Python $ProcessInspector --pid $ProcessIds[0] --root $ProjectRoot --database $Database --storage $Storage 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Port $Port is used by a different or unverifiable installation. No process was stopped. Details: $Inspection" }
    Write-Output $Inspection
}

try {
    [System.IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
    $Hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Key = [System.BitConverter]::ToString($Hash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant()))) -replace '-', ''
    } finally { $Hash.Dispose() }
    $Mutex = [System.Threading.Mutex]::new($false, "Local\PV1Site-$Key-$Port")
    try { $OwnsMutex = $Mutex.WaitOne(45000) }
    catch [System.Threading.AbandonedMutexException] { $OwnsMutex = $true }
    if (-not $OwnsMutex) { throw 'Another PV1 launcher is still starting the site. Try again shortly.' }

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python environment is missing: $Python" }
    if (-not (Test-Path -LiteralPath $ProcessInspector -PathType Leaf)) { throw "Process inspector is missing: $ProcessInspector" }
    if (@(Get-SiteListeners).Count -gt 0 -or (Test-PortInUse)) {
        Confirm-SiteProcess
        if (-not (Test-SiteReady)) { throw "The verified PV1 listener is unavailable. Inspect $RuntimeRoot. No process was stopped." }
    } else {
        if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) { throw "Main database is missing: $Database" }
        if (-not (Test-Path -LiteralPath $Storage -PathType Container)) { throw "Main file storage is missing: $Storage" }

        $env:DATABASE_URL = 'sqlite:///' + ($Database -replace '\\', '/')
        $env:PV_STORAGE_ROOT = $Storage
        $env:PV_APP_MODE = 'prod'
        $env:PV_USER_SWITCH_ENABLED = 'false'
        $env:PV_ALLOW_QUERY_USER_SWITCH = 'false'
        $env:PV_AUTH_COOKIE_SECURE = 'false'
        $env:PYTHONUTF8 = '1'
        $env:PYTHONDONTWRITEBYTECODE = '1'
        $env:PV_AUTH_COOKIE_NAME = 'pv_session'
        $env:PV_INSTANCE_LABEL = 'Объединённая рабочая версия — ваши данные'
        Remove-Item Env:PV_AS2_CONFIG -ErrorAction SilentlyContinue
        Remove-Item Env:PV_RESTORE_PATH_MAP -ErrorAction SilentlyContinue
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

        $Check = Start-Process -FilePath $Python -ArgumentList @('-m', 'app.manage', 'check-db') -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RuntimeRoot "check-$Port.stdout.log") -RedirectStandardError (Join-Path $RuntimeRoot "check-$Port.stderr.log") -Wait -PassThru
        if ($Check.ExitCode -ne 0) { throw "Main database validation failed. See $RuntimeRoot\check-$Port.stderr.log. No migration was performed." }

        $Server = Start-Process -FilePath $Python -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $Port) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RuntimeRoot "server-$Port.stdout.log") -RedirectStandardError (Join-Path $RuntimeRoot "server-$Port.stderr.log") -PassThru
        $Deadline = [System.Diagnostics.Stopwatch]::StartNew()
        while (-not (Test-SiteReady)) {
            if ($Server.HasExited -or $Deadline.Elapsed.TotalSeconds -gt 40) {
                throw "PV1 did not become ready. See $RuntimeRoot\server-$Port.stderr.log."
            }
            Start-Sleep -Milliseconds 400
        }
        Confirm-SiteProcess
    }

    if (-not $NoBrowser) { Start-Process -FilePath $Url }
    Add-Content -LiteralPath (Join-Path $RuntimeRoot 'launcher.log') -Encoding UTF8 -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " Ready $Url (browser=" + (-not $NoBrowser) + ')')
    Write-Output "PV1 is ready: $Url"
} catch {
    $Message = $_.Exception.Message
    if (Test-Path -LiteralPath $RuntimeRoot) {
        Add-Content -LiteralPath (Join-Path $RuntimeRoot 'launcher.log') -Encoding UTF8 -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message)
    }
    if (-not $NoBrowser) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($Message, 'PV1 startup error', 'OK', 'Error') | Out-Null
    }
    [Console]::Error.WriteLine($Message)
    exit 1
} finally {
    if ($OwnsMutex) { $Mutex.ReleaseMutex() }
    if ($Mutex) { $Mutex.Dispose() }
}
