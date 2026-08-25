<#
.SYNOPSIS
    Launches the CBR-to-OBS Migration local web app.

.DESCRIPTION
    Installs Python dependencies if needed, starts a FastAPI server
    on localhost, and opens the browser automatically.

.PARAMETER Port
    Server port (default: 8080)

.PARAMETER NoBrowser
    Don't open browser automatically

.EXAMPLE
    .\run.ps1
    .\run.ps1 -Port 9090
    .\run.ps1 -NoBrowser
#>

param(
    [int]$Port = 8080,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$appPath = Join-Path $projectRoot "run.py"

Write-Host ""
Write-Host "=== CBR-to-OBS Migration Tool ===" -ForegroundColor Cyan
Write-Host ""

# Cerrar cualquier instancia anterior escuchando en el puerto
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $existing) {
    try {
        $procId = $conn.OwningProcess
        $pname = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Write-Host "Cerrando instancia anterior en puerto ${Port}: $pname (PID $procId)" -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    } catch { }
}

$pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $pythonCmd) { $pythonCmd = (Get-Command python3 -ErrorAction SilentlyContinue) }
if (-not $pythonCmd) { $pythonCmd = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $pythonCmd) {
    Write-Host "ERROR: Python no encontrado. Instala Python 3.9+ desde https://python.org" -ForegroundColor Red
    exit 1
}
$py = $pythonCmd.Source

$args = @($appPath, "--port", $Port)
if ($NoBrowser) { $args += "--no-browser" }

& $py @args
