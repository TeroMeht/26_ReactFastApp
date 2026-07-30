# wait_for_services.ps1
#
# Poll the FastAPI backend and the Next.js frontend until both respond,
# or the total timeout expires. Called from start.bat between launching
# the two windows and opening the browser — so the browser only opens
# on a live app, not on a "site can't be reached" page.
#
# Exit code contract:
#   0  both services responded within the timeout
#   1  one or both did not respond in time

param(
    # Probe by IPv4 literal, NOT "localhost". Windows 11 resolves
    # localhost to ::1 (IPv6) first; uvicorn binds 127.0.0.1 (IPv4)
    # only, so every request to http://localhost:8000 goes to nowhere.
    # Next.js dual-binds so it may still respond on the v6 stack —
    # which is why the frontend probe "works" over localhost while
    # the backend never does. Hitting 127.0.0.1 sidesteps the whole
    # resolution question. Override via -BackendHost / -FrontendHost.
    [string]$BackendHost  = "127.0.0.1",
    [string]$FrontendHost = "127.0.0.1",
    [int]$BackendPort     = 8000,
    [int]$FrontendPort    = 3000,
    [string]$BackendPath  = "/openapi.json",   # always present when FastAPI is up
    [string]$FrontendPath = "/",               # Next.js serves the index page
    [int]$TimeoutSec        = 90,              # generous for cold IB / DB / build
    [int]$PollMs            = 500,
    [int]$RequestTimeoutSec = 2                # per-request curl-style timeout
)

function Log($msg) { Write-Host "[wait_for_services] $msg" }

$backendUrl  = "http://${BackendHost}:${BackendPort}${BackendPath}"
$frontendUrl = "http://${FrontendHost}:${FrontendPort}${FrontendPath}"

function Test-Ready {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest `
                -Uri $Url `
                -UseBasicParsing `
                -TimeoutSec $RequestTimeoutSec `
                -ErrorAction Stop
        # Accept anything that isn't a 5xx — Next.js may 404 the exact
        # path in dev but that still proves the server is up.
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
        return $false
    }
}

Log "Backend  : $backendUrl"
Log "Frontend : $frontendUrl"
Log "Timeout  : ${TimeoutSec}s (poll every ${PollMs}ms)"

$deadline      = (Get-Date).AddSeconds($TimeoutSec)
$backendReady  = $false
$frontendReady = $false
$lastPrinted   = -1

while (-not ($backendReady -and $frontendReady) -and (Get-Date) -lt $deadline) {

    if (-not $backendReady) {
        $backendReady = Test-Ready $backendUrl
        if ($backendReady) { Log "Backend UP" }
    }
    if (-not $frontendReady) {
        $frontendReady = Test-Ready $frontendUrl
        if ($frontendReady) { Log "Frontend UP" }
    }

    if ($backendReady -and $frontendReady) { break }

    # Progress ping every ~5s so the user knows we're still trying.
    $elapsed = [int]((Get-Date) - $deadline.AddSeconds(-$TimeoutSec)).TotalSeconds
    if ($elapsed -ne $lastPrinted -and ($elapsed % 5 -eq 0)) {
        $status = @()
        $status += if ($backendReady)  { "backend=UP"  } else { "backend=wait" }
        $status += if ($frontendReady) { "frontend=UP" } else { "frontend=wait" }
        Log ("t+{0:D2}s  {1}" -f $elapsed, ($status -join "  "))
        $lastPrinted = $elapsed
    }

    Start-Sleep -Milliseconds $PollMs
}

if ($backendReady -and $frontendReady) {
    Log "Both services responded. OK to open browser."
    exit 0
}

if (-not $backendReady)  { Log "Backend did NOT respond within ${TimeoutSec}s at $backendUrl" }
if (-not $frontendReady) { Log "Frontend did NOT respond within ${TimeoutSec}s at $frontendUrl" }
Log "Aborting. Check the Backend / Frontend windows for errors."
exit 1
