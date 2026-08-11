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

function Load-EnvFile {
    param([string]$EnvFile)
    if (Test-Path $EnvFile) {
        Write-Host "Loading environment variables from $EnvFile" -ForegroundColor Gray
        Get-Content $EnvFile | ForEach-Object {
            if ($_ -match '^\s*([A-Z_]+)=(.*)') {
                $name = $matches[1]
                $value = $matches[2].Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
}

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
    Load-EnvFile (Join-Path $ProjectRoot ".env")
    $venvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
    & $venvActivate
    python -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort
}

function Start-Frontend {
    Write-Host "`nStarting frontend on port $FrontendPort..." -ForegroundColor Cyan
    cd $FrontendDir
    npm run dev
}

# --- Main ---
Write-Host "Health Connect Dev Server" -ForegroundColor Magenta
Write-Host "=========================" -ForegroundColor Magenta

$venvActivate = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"

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
        
        $backendCmd = "& '$venvActivate'; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort"
        $frontendCmd = "cd '$FrontendDir'; npm run dev"
        
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd
        
        Write-Host "Backend starting on port $BackendPort..." -ForegroundColor Green
        Write-Host "Frontend starting on port $FrontendPort..." -ForegroundColor Green
        Write-Host "Two new terminal windows should open for each service." -ForegroundColor Gray
    }
}

Write-Host "`nDone!" -ForegroundColor Green
