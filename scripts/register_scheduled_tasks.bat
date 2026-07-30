@echo off
REM One-shot registration of the risk-env lock / unlock jobs in Windows
REM Task Scheduler. Run this ONCE from an elevated cmd (right-click
REM > "Run as administrator") after editing lock_risk_env.bat and
REM unlock_risk_env.bat if you need different paths.
REM
REM Times are the machine's LOCAL time. This assumes the Windows box is
REM set to Helsinki (Europe/Helsinki). If your box is on a different
REM timezone, edit the /ST values below.
REM
REM Schedule:
REM   Lock   Mon-Fri 16:30  (two minutes before FIRST_ENTRY_HOUR=16:32)
REM   Unlock Mon-Fri 23:05  (five minutes after US regular-session close)
REM
REM /RL HIGHEST runs the task elevated so icacls has permission to modify
REM the ACL. /F overwrites any existing task with the same name so this
REM script is safely re-runnable.

setlocal
set "SCRIPT_DIR=%~dp0"
set "LOCK_SCRIPT=%SCRIPT_DIR%lock_risk_env.bat"
set "UNLOCK_SCRIPT=%SCRIPT_DIR%unlock_risk_env.bat"

if not exist "%LOCK_SCRIPT%" (
  echo [register] Missing %LOCK_SCRIPT%
  exit /b 1
)
if not exist "%UNLOCK_SCRIPT%" (
  echo [register] Missing %UNLOCK_SCRIPT%
  exit /b 1
)

echo [register] Installing lock task...
schtasks /Create ^
  /TN "RiskEnv_Lock" ^
  /TR "\"%LOCK_SCRIPT%\"" ^
  /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 16:30 ^
  /RL HIGHEST /F
if errorlevel 1 (
  echo [register] FAILED to install lock task
  exit /b 1
)

echo [register] Installing unlock task...
schtasks /Create ^
  /TN "RiskEnv_Unlock" ^
  /TR "\"%UNLOCK_SCRIPT%\"" ^
  /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
  /ST 23:05 ^
  /RL HIGHEST /F
if errorlevel 1 (
  echo [register] FAILED to install unlock task
  exit /b 1
)

echo.
echo [register] Done. Verify with:
echo   schtasks /Query /TN RiskEnv_Lock   /V /FO LIST
echo   schtasks /Query /TN RiskEnv_Unlock /V /FO LIST
echo.
echo [register] Manual fire (for smoke-test):
echo   schtasks /Run /TN RiskEnv_Lock
echo   schtasks /Run /TN RiskEnv_Unlock
echo.
echo [register] Uninstall (both):
echo   schtasks /Delete /TN RiskEnv_Lock   /F
echo   schtasks /Delete /TN RiskEnv_Unlock /F
endlocal
