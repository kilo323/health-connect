param(
    [ValidateSet("frontend", "backend", "")]
    [string]$Service = ""
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "app"
$FrontendDir = Join-Path $ProjectRoot "app\frontend"
$PidFile = Join-Path $ProjectRoot ".dev-pids.json"

# Resolve ports from process env first, then .env, else default 8000/3000.
# (Read .env inline here since the Load-EnvFile function is defined below.)
function Get-PortFromEnv {
    param([string]$Name, [int]$Default)
    $val = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $val) {
        $envFile = Join-Path $ProjectRoot ".env"
        if (Test-Path $envFile) {
            $line = Get-Content $envFile | Where-Object { $_ -match "^\s*$Name=" } | Select-Object -First 1
            if ($line) { $val = ($line -split '=', 2)[1].Trim('"').Trim("'").Trim() }
        }
    }
    if ($val) { return [int]$val }
    return $Default
}
$BackendPort = Get-PortFromEnv -Name "BACKEND_PORT" -Default 8000
$FrontendPort = Get-PortFromEnv -Name "FRONTEND_PORT" -Default 3000

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

function Save-ServicePids {
    param(
        [string]$Name,
        [int]$WindowPid
    )
    $existing = @()
    if (Test-Path $PidFile) {
        # PS 5.1: @(...) around ConvertFrom-Json collapses top-level arrays into
        # one element, so force enumeration first with ForEach-Object { $_ }.
        try { $existing = @((Get-Content $PidFile -Raw | ConvertFrom-Json | ForEach-Object { $_ })) } catch { $existing = @() }
    }
    # Replace any stale record for the same service instead of appending duplicates.
    $existing = @($existing | Where-Object { $_.name -ne $Name })
    $existing += [PSCustomObject]@{ name = $Name; pids = @($WindowPid) }
    $existing | ConvertTo-Json -Depth 4 | Set-Content $PidFile -Encoding UTF8
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
        
        $backendProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd -PassThru
        $frontendProc = Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd -PassThru
        
        Save-ServicePids -Name "Backend" -WindowPid $backendProc.Id
        Save-ServicePids -Name "Frontend" -WindowPid $frontendProc.Id
        
        Write-Host "Backend starting on port $BackendPort..." -ForegroundColor Green
        Write-Host "Frontend starting on port $FrontendPort..." -ForegroundColor Green
        Write-Host "Two new terminal windows should open for each service." -ForegroundColor Gray
    }
}

Write-Host "`nDone!" -ForegroundColor Green
