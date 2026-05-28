<#
.SYNOPSIS
    Launch the local FastAPI backend and Vite dev UI on Windows.

.DESCRIPTION
    Windows PowerShell counterpart to deploy/test.sh. Resolves a Python
    interpreter (active venv -> repo .venv -> py launcher -> python on PATH),
    ensures backend and frontend dependencies are installed, starts uvicorn in
    the background, waits for /api/health, then runs the Vite dev server in
    the foreground. Cleans up the backend on exit.

.PARAMETER Host
    Bind host for the Vite UI. Defaults to env:HOST or 0.0.0.0.

.PARAMETER Port
    Bind port for the Vite UI. Defaults to env:PORT or 5173.

.PARAMETER BackendHost
    Bind host for FastAPI. Defaults to env:BACKEND_HOST or 127.0.0.1.

.PARAMETER BackendPort
    Bind port for FastAPI. Defaults to env:BACKEND_PORT or 8000.

.EXAMPLE
    pwsh deploy/test.ps1

.EXAMPLE
    pwsh deploy/test.ps1 -Port 5174 -BackendPort 8001
#>

[CmdletBinding()]
param(
    [string]$BindHost = $(if ($env:HOST) { $env:HOST } else { '0.0.0.0' }),
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 5173 }),
    [string]$BackendHost = $(if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { '127.0.0.1' }),
    [int]$BackendPort = $(if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 })
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Resolve-Path (Join-Path $ScriptDir '..')
$FrontendDir = Join-Path $RepoDir 'frontend'
$BackendUrl = if ($env:VITE_BACKEND_URL) { $env:VITE_BACKEND_URL } else { "http://${BackendHost}:${BackendPort}" }

if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) {
    Write-Error "frontend/package.json not found at $FrontendDir"
    exit 1
}

function Find-Python {
    if ($env:VIRTUAL_ENV) {
        $venvPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
        if (Test-Path $venvPython) { return $venvPython }
    }

    $repoVenv = Join-Path $RepoDir '.venv\Scripts\python.exe'
    if (Test-Path $repoVenv) { return $repoVenv }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }

    Write-Error @"
No Python interpreter found.
Create a virtual environment first:
    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r backend\requirements.txt
"@
    exit 1
}

function Test-BackendHealthy {
    try {
        $response = Invoke-WebRequest -Uri "$BackendUrl/api/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-Backend {
    for ($i = 1; $i -le 60; $i++) {
        if (Test-BackendHealthy) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    Write-Host "Installing frontend dependencies"
    if (Test-Path (Join-Path $FrontendDir 'package-lock.json')) {
        npm --prefix $FrontendDir ci --no-audit --no-fund
    } else {
        npm --prefix $FrontendDir install --no-audit --no-fund
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "npm install failed"
        exit 1
    }
}

$PythonBin = Find-Python
Write-Host "Using Python: $PythonBin"

& $PythonBin -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing backend Python dependencies"
    & $PythonBin -m pip install -r (Join-Path $RepoDir 'backend\requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed"
        exit 1
    }
}

$BackendProcess = $null
$startedBackend = $false

try {
    if (Test-BackendHealthy) {
        Write-Host "Using existing backend at $BackendUrl"
    } else {
        Write-Host "Starting backend at $BackendUrl"
        $BackendProcess = Start-Process `
            -FilePath $PythonBin `
            -ArgumentList '-m', 'uvicorn', 'backend.src.server:app', '--host', $BackendHost, '--port', $BackendPort, '--reload' `
            -WorkingDirectory $RepoDir `
            -NoNewWindow `
            -PassThru
        $startedBackend = $true

        if (-not (Wait-Backend)) {
            Write-Error "backend did not become healthy at $BackendUrl"
            exit 1
        }
    }

    Write-Host "Starting Vite UI at http://localhost:$Port"
    $env:VITE_BACKEND_URL = $BackendUrl
    npm --prefix $FrontendDir run dev:ui -- --host $BindHost --port $Port
} finally {
    if ($startedBackend -and $BackendProcess -and -not $BackendProcess.HasExited) {
        Write-Host "Stopping backend (PID $($BackendProcess.Id))"
        try {
            Stop-Process -Id $BackendProcess.Id -Force -ErrorAction Stop
        } catch {
            Write-Warning "Could not stop backend process: $_"
        }
    }
}
