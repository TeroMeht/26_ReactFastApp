@echo off
setlocal
set ROOT=%~dp0

echo Starting FastAPI Backend...
start "Backend" cmd /k "cd /d %ROOT%backend && python -m uvicorn main:app"

echo Starting Next.js Frontend...
REM frontend_start.bat skips `npm run build` when nothing under
REM app/components/constants/lib/generated/public/ (or any watched root
REM config file) has changed since the last successful build.
start "Frontend" cmd /k "%ROOT%scripts\frontend_start.bat"

echo Waiting for backend and frontend to respond...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\wait_for_services.ps1"
if errorlevel 1 (
  echo.
  echo Services did not start cleanly. NOT opening the browser.
  echo Check the Backend and Frontend windows for errors, then start the browser manually if desired.
  pause
  exit /b 1
)

echo Opening Browser...
start "" http://localhost:3000

endlocal
