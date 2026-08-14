param(
    [ValidateSet("frontend", "backend", "")]
    [string]$Service = ""
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".dev-pids.json"
$BackendPort = 8000
$FrontendPort = 3000

# Executables that belong to a dev service tree (uvicorn reloader/worker, npm/node)
$ServiceProcessNames = @("python.exe", "node.exe", "npm.exe", "cmd.exe")

function Kill-Tree {
    param([int]$RootPid)
    # Collect the full descendant set first, then kill deepest-first so the
    # uvicorn reloader can't respawn a new worker before it is killed itself.
    # NOTE: Win32_Process.ProcessId/ParentProcessId are UInt32; normalize to Int32
    # so hashtable key lookups match.
    $children = @{}
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $parent = [int]$_.ParentProcessId
        $childPid = [int]$_.ProcessId
        if ($parent -gt 0) {
            if (-not $children.ContainsKey($parent)) {
                $children[$parent] = New-Object System.Collections.Generic.List[int]
            }
            $children[$parent].Add($childPid)
        }
    }
    $descendants = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $pidToVisit = $queue.Dequeue()
        if ($children.ContainsKey($pidToVisit)) {
            foreach ($childPid in $children[$pidToVisit]) {
                $descendants.Add($childPid)
                $queue.Enqueue($childPid)
            }
        }
    }
    $order = @($descendants) + @($RootPid)
    for ($i = $order.Count - 1; $i -ge 0; $i--) {
        Stop-Process -Id $order[$i] -Force -ErrorAction SilentlyContinue
    }
}

function Stop-PidFileServices {
    param([string]$Filter)
    $stopped = @()
    $records = @()
    $fileRead = $false
    if (Test-Path $PidFile) {
        try {
            # PS 5.1: @(...) around ConvertFrom-Json collapses top-level arrays into
            # one element, so force enumeration first with ForEach-Object { $_ }.
            $records = @((Get-Content $PidFile -Raw | ConvertFrom-Json | ForEach-Object { $_ }))
            $fileRead = $true
        } catch {
            Write-Host "Could not read the PID file: $_" -ForegroundColor DarkGray
        }
    }
    $remaining = @()
    foreach ($rec in $records) {
        $name = $rec.name
        $pids = @($rec.pids)
        if ($Filter -and $name -ne $Filter) {
            $remaining += $rec
            continue
        }
        $stoppedNow = $false
        foreach ($pidValue in $pids) {
            $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "Stopping $name (PID: $pidValue)..." -ForegroundColor Yellow
                Kill-Tree -RootPid $pidValue
                $stoppedNow = $true
            }
        }
        if ($stoppedNow) { $stopped += $name }
        if (-not $stoppedNow) { $remaining += $rec }
    }
    # Rewrite the PID file without the stopped services (keeps Frontend when
    # only Backend is stopped, etc.). Remove the file entirely if nothing remains.
    if ($fileRead) {
        if ($remaining.Count -gt 0) {
            $remaining | ConvertTo-Json -Depth 4 | Set-Content $PidFile -Encoding UTF8
        } else {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        }
    }
    return ($stopped | Select-Object -Unique)
}

function Stop-OneService {
    param([string]$Name, [int]$Port, [string[]]$StoppedFromPidFile)
    if ($Name -in $StoppedFromPidFile) { return }
    # Fallback: kill the whole tree owning the port, not just the socket owner.
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Write-Host "Stopping $Name (PID: $($conn.OwningProcess), Port: $Port)..." -ForegroundColor Yellow
        Kill-Tree -RootPid $conn.OwningProcess
    } else {
        Write-Host "$Name was not running on port $Port." -ForegroundColor DarkGray
    }
}

# --- Main ---
Write-Host "Health Connect - Stop Services" -ForegroundColor Magenta
Write-Host "===============================" -ForegroundColor Magenta

switch ($Service) {
    "backend" {
        $stoppedFromPidFile = Stop-PidFileServices -Filter "Backend"
        Stop-OneService -Name "Backend" -Port $BackendPort -StoppedFromPidFile $stoppedFromPidFile
    }
    "frontend" {
        $stoppedFromPidFile = Stop-PidFileServices -Filter "Frontend"
        Stop-OneService -Name "Frontend" -Port $FrontendPort -StoppedFromPidFile $stoppedFromPidFile
    }
    default {
        $stoppedFromPidFile = Stop-PidFileServices -Filter $null
        Stop-OneService -Name "Backend" -Port $BackendPort -StoppedFromPidFile $stoppedFromPidFile
        Stop-OneService -Name "Frontend" -Port $FrontendPort -StoppedFromPidFile $stoppedFromPidFile
    }
}

Write-Host "`nDone!" -ForegroundColor Green
