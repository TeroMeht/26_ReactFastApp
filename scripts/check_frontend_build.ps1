# check_frontend_build.ps1
#
# Decide whether the Next.js frontend needs a rebuild by comparing the
# modification time of every watched source file against the timestamp
# of frontend/.next/BUILD_ID (which Next.js rewrites on every successful
# build).
#
# Exit code contract:
#   0  build is up-to-date, skip `npm run build`
#   1  rebuild needed (source changed, or no previous build exists)
#
# Fast path: uses raw .NET Directory.EnumerateFiles + File.GetLastWriteTime
# (no PowerShell object materialization) and short-circuits the moment
# a single file newer than BUILD_ID is found. In the common case where
# you changed one component, the scan stops after a few files instead
# of walking the whole tree.
#
# Called from scripts\frontend_start.bat.

param(
    [string]$FrontendDir = (Join-Path $PSScriptRoot "..\frontend")
)

function Log($msg) { Write-Host "[check_frontend_build] $msg" }

$sw = [System.Diagnostics.Stopwatch]::StartNew()

$FrontendDir = (Resolve-Path $FrontendDir).Path
$buildId = Join-Path $FrontendDir ".next\BUILD_ID"

Log "Frontend dir: $FrontendDir"

if (-not (Test-Path $buildId)) {
    Log "No .next/BUILD_ID found - full build needed."
    exit 1
}

$buildTime = [System.IO.File]::GetLastWriteTime($buildId)
$buildStr  = $buildTime.ToString("yyyy-MM-dd HH:mm:ss")
Log "BUILD_ID: $buildStr"

# Directories walked recursively.
$watchDirs = @("app", "components", "constants", "lib", "generated", "public")

# Individual config files at the frontend root.
$watchFiles = @(
    "package.json", "package-lock.json",
    "next.config.ts", "next.config.js", "next.config.mjs",
    "tailwind.config.ts", "postcss.config.mjs",
    "tsconfig.json", "globals.css"
)

# Resolve to concrete existing paths.
$dirPaths  = @()
$filePaths = @()
foreach ($d in $watchDirs) {
    $p = Join-Path $FrontendDir $d
    if (Test-Path $p -PathType Container) { $dirPaths += $p }
}
foreach ($f in $watchFiles) {
    $p = Join-Path $FrontendDir $f
    if (Test-Path $p -PathType Leaf) { $filePaths += $p }
}

if ($dirPaths.Count -eq 0 -and $filePaths.Count -eq 0) {
    Log "No watched paths under $FrontendDir - assuming rebuild."
    exit 1
}

$scanned      = 0
$newestSeenAt = [DateTime]::MinValue
$newestSeenP  = $null
$trigger      = $null   # first file found newer than BUILD_ID

# Walk root config files first (cheap, and they change often).
foreach ($p in $filePaths) {
    $scanned++
    $mt = [System.IO.File]::GetLastWriteTime($p)
    if ($mt -gt $newestSeenAt) { $newestSeenAt = $mt; $newestSeenP = $p }
    if ($mt -gt $buildTime) { $trigger = $p; break }
}

# Walk directories via raw .NET enumeration. This avoids materializing
# a FileInfo object per file, which is where Get-ChildItem gets slow.
if (-not $trigger) {
    foreach ($d in $dirPaths) {
        try {
            $enum = [System.IO.Directory]::EnumerateFiles(
                $d, "*", [System.IO.SearchOption]::AllDirectories)
            foreach ($f in $enum) {
                $scanned++
                $mt = [System.IO.File]::GetLastWriteTime($f)
                if ($mt -gt $newestSeenAt) { $newestSeenAt = $mt; $newestSeenP = $f }
                if ($mt -gt $buildTime) { $trigger = $f; break }
            }
        } catch {
            Log "  warn: could not enumerate $d ($($_.Exception.Message))"
        }
        if ($trigger) { break }
    }
}

$sw.Stop()
$elapsed = "{0:F0} ms" -f $sw.Elapsed.TotalMilliseconds

if ($trigger) {
    $tRel = $trigger.Substring($FrontendDir.Length + 1)
    $tStr = [System.IO.File]::GetLastWriteTime($trigger).ToString("yyyy-MM-dd HH:mm:ss")
    Log "Scanned $scanned files in $elapsed (early-exit)."
    Log "DECISION: rebuild needed. $tRel ($tStr) > BUILD_ID ($buildStr)"
    exit 1
}

$nRel = if ($newestSeenP) { $newestSeenP.Substring($FrontendDir.Length + 1) } else { "(none)" }
$nStr = if ($newestSeenP) { $newestSeenAt.ToString("yyyy-MM-dd HH:mm:ss") } else { "(none)" }
Log "Scanned $scanned files in $elapsed (full scan)."
Log "DECISION: up to date. Newest source: $nRel ($nStr) <= BUILD_ID ($buildStr). Skipping build."
exit 0
