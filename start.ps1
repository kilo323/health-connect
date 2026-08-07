param(
    [ValidateSet("frontend", "backend", "")]
    [string]$Service = ""
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "app"
$FrontendDir = Join-Path $ProjectRoot "app\frontend"
$BackendPort = 8000
$FrontendPort = 3000

function Stop-ServiceByPort {
    param([int]$Port, [string]$Name)
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" }
    if ($connections) {
        foreach ($conn in $connections) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "Stopping $Name (PID: $($proc.Id), Port: $Port)..." -ForegroundColor Yellow
                Stop-Process -Id $proc.Id -Force
                Start-Sleep -Seconds 1
            }
        }
        Write-Host "$Name stopped." -ForegroundColor Green
    } else {
        Write-Host "$Name was not running on port $Port." -ForegroundColor DarkGray
    }
}

function Start-Backend {
    Write-Host "`nStarting backend on port $BackendPort..." -ForegroundColor Cyan
    $venvActivate = Join-Path $BackendDir ".venv\Scripts\Activate.ps1"
    $startCmd = "& '$venvActivate'; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort"
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command", "cd '$ProjectRoot'; $startCmd"
    ) -WindowStyle Normal
    Write-Host "Backend started in a new window." -ForegroundColor Green
}

function Start-Frontend {
    Write-Host "`nStarting frontend on port $FrontendPort..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command", "cd '$FrontendDir'; npm run dev"
    ) -WindowStyle Normal
    Write-Host "Frontend started in a new window." -ForegroundColor Green
}

# --- Main ---
Write-Host "Health Connect Dev Server" -ForegroundColor Magenta
Write-Host "=========================" -ForegroundColor Magenta

switch ($Service) {
    "backend" {
        Stop-ServiceByPort -Port $BackendPort -Name "Backend"
        Start-Backend
    }
    "frontend" {
        Stop-ServiceByPort -Port $FrontendPort -Name "Frontend"
        Start-Frontend
    }
    default {
        Stop-ServiceByPort -Port $BackendPort -Name "Backend"
        Stop-ServiceByPort -Port $FrontendPort -Name "Frontend"
        Start-Backend
        Start-Frontend
    }
}

Write-Host "`nDone!" -ForegroundColor Green
