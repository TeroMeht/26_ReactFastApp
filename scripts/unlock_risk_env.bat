@echo off
REM Remove the deny ACE placed by lock_risk_env.bat.
REM Scheduled to run at market close (Mon-Fri 23:05 Helsinki) by
REM register_scheduled_tasks.bat.

set "TARGET=C:\codebase\env-repo\26_risk_manager.env"

if not exist "%TARGET%" (
  echo [unlock_risk_env] TARGET not found: %TARGET%
  exit /b 1
)

icacls "%TARGET%" /remove:d "%USERNAME%" /Q
if errorlevel 1 (
  echo [unlock_risk_env] FAILED to unlock %TARGET%
  exit /b 1
)

echo [unlock_risk_env] Unlocked %TARGET% at %DATE% %TIME%
