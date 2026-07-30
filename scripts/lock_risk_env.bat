@echo off
REM Deny write/delete/attribute-write on 26_risk_manager.env for the current
REM user. Scheduled to run at market open (Mon-Fri 16:30 Helsinki) by
REM register_scheduled_tasks.bat.
REM
REM Idempotent: running twice just adds a duplicate deny ACE, which is
REM functionally the same as one.

set "TARGET=C:\codebase\env-repo\26_risk_manager.env"

if not exist "%TARGET%" (
  echo [lock_risk_env] TARGET not found: %TARGET%
  exit /b 1
)

REM Use fine-grained specific rights only. The simple letter W inside
REM parens expands to GENERIC_WRITE which includes SYNCHRONIZE, and
REM denying SYNCHRONIZE breaks all reads (Python's open(), Notepad
REM read-only, everything). WD/AD/WA/WEA/DE are the specific
REM write/append/attr/delete bits and leave reads untouched.
icacls "%TARGET%" /deny "%USERNAME%":(WD,AD,WA,WEA,DE) /Q
if errorlevel 1 (
  echo [lock_risk_env] FAILED to lock %TARGET%
  exit /b 1
)

echo [lock_risk_env] Locked %TARGET% at %DATE% %TIME%
