param(
    [ValidateSet("frontend", "backend", "")]
    [string]$Service = ""
)

$ErrorActionPreference = "SilentlyContinue"
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

# --- Main ---
Write-Host "Health Connect - Stop Services" -ForegroundColor Magenta
Write-Host "===============================" -ForegroundColor Magenta

switch ($Service) {
    "backend" {
        Stop-ServiceByPort -Port $BackendPort -Name "Backend"
    }
    "frontend" {
        Stop-ServiceByPort -Port $FrontendPort -Name "Frontend"
    }
    default {
        Stop-ServiceByPort -Port $BackendPort -Name "Backend"
        Stop-ServiceByPort -Port $FrontendPort -Name "Frontend"
    }
}

Write-Host "`nDone!" -ForegroundColor Green
