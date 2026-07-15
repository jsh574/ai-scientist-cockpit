param(
    [string]$HostAddress = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logs = Join-Path $root "logs"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found. Create .venv and install backend/requirements.txt first."
}

$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
New-Item -ItemType Directory -Path $logs -Force | Out-Null

$backend = Start-Process `
    -FilePath $python `
    -ArgumentList @(
        "-m", "uvicorn", "backend.app.main:app",
        "--host", $HostAddress,
        "--port", $BackendPort
    ) `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $logs "backend.stdout.log") `
    -RedirectStandardError (Join-Path $logs "backend.stderr.log") `
    -WindowStyle Hidden `
    -PassThru

try {
    Start-Sleep -Seconds 1
    $backend.Refresh()
    if ($backend.HasExited) {
        $errorLog = Get-Content (Join-Path $logs "backend.stderr.log") -Raw -ErrorAction SilentlyContinue
        throw "Backend failed to start. $errorLog"
    }

    Write-Host "Backend: http://${HostAddress}:${BackendPort}"
    Write-Host "Frontend: http://${HostAddress}:${FrontendPort}"
    Write-Host "OpenAPI: http://${HostAddress}:${BackendPort}/docs"
    Write-Host "MCP: .\.venv\Scripts\python.exe -m backend.mcp_server"
    & $npm run dev -- --host $HostAddress --port $FrontendPort
}
finally {
    $backend.Refresh()
    if (-not $backend.HasExited) {
        Stop-Process -Id $backend.Id
    }
}
