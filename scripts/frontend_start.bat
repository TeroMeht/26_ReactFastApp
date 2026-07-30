@echo off
REM Launches the Next.js frontend. Runs `npm run build` only when the
REM check script reports source has changed since the last build; skips
REM the build entirely when nothing changed. Always ends with
REM `npm start -- -p 3000`.

setlocal
set "SCRIPT_DIR=%~dp0"
set "FRONTEND_DIR=%SCRIPT_DIR%..\frontend"

cd /d "%FRONTEND_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%check_frontend_build.ps1" -FrontendDir "%FRONTEND_DIR%"
if errorlevel 1 (
  echo [frontend_start] Running: npm run build
  call npm run build
  if errorlevel 1 (
    echo [frontend_start] Build failed. Aborting.
    exit /b 1
  )
) else (
  echo [frontend_start] Build up-to-date. Skipping npm run build.
)

echo [frontend_start] Running: npm start -- -p 3000
npm start -- -p 3000

endlocal
