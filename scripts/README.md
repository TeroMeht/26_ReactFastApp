# Risk Manager Freeze — Setup Guide

Locks `26_risk_manager.env` during market hours so risk parameters
(position size, daily loss cap, cooldowns, etc.) cannot be edited in the
heat of a session. Ulysses-pact style self-binding: the tighter
constraint applies exactly when your judgment is worst.

## How the defense is layered

Two independent mechanisms, both required:

1. **In-process freeze** — `backend/core/risk_manager_config.py`
   declares `RiskManagerSettings(BaseSettings)` with `frozen = True` in
   its inner `Config`. The module-level singleton `risk_settings` is
   built once at FastAPI startup; any code that tries
   `risk_settings.RISK = 999` mid-session raises `ValidationError`
   before the write lands. Callers use attribute access, e.g.
   `risk_settings.MAX_DAILY_LOSS`, exactly like the existing `settings`
   singleton from `config.py`.

2. **OS-level ACL lock** — during market hours, Windows Task Scheduler
   runs `lock_risk_env.bat` which adds a Deny ACE for the current user
   on write/delete/attribute-write. Even Notepad-as-admin refuses to
   save until the ACE is removed. `unlock_risk_env.bat` reverses this
   at market close.

The freeze alone would let a determined user bounce the FastAPI
process to reload edited values; the ACL alone would let in-process
code re-read the file. Together they close both loops.

## Prerequisites

- Windows machine, timezone set to Helsinki. Verify with `tzutil /g`
  — should print `FLE Standard Time`. DST is handled automatically.
- `C:\codebase\env-repo\26_risk_manager.env` exists and contains the
  full set of risk keys (see `backend/core/risk_manager_config.py` for
  the required fields).
- FastAPI backend imports `risk_settings` from `core.risk_manager_config`
  and never re-reads the env file at runtime.

## One-time setup

Run these steps once per machine.

1. Open **cmd.exe** as administrator (right-click → "Run as
   administrator"). Admin rights are needed because Task Scheduler
   registers tasks with `/RL HIGHEST` so `icacls` has permission to
   modify the ACL on the env file.

2. Register both scheduled tasks:

   ```
   cd C:\codebase\prod\26_ReactFastApp\scripts
   register_scheduled_tasks.bat
   ```

3. Confirm both tasks are installed and have a next-run time:

   ```
   schtasks /Query /TN RiskEnv_Lock   /V /FO LIST | findstr /I "Next Run Time"
   schtasks /Query /TN RiskEnv_Unlock /V /FO LIST | findstr /I "Next Run Time"
   ```

## Default schedule

| Task              | When (Helsinki local)     | What it does                       |
| ----------------- | ------------------------- | ---------------------------------- |
| `RiskEnv_Lock`    | Mon–Fri 16:30             | Adds Deny ACE to `26_risk_manager.env` |
| `RiskEnv_Unlock`  | Mon–Fri 23:05             | Removes the Deny ACE               |

16:30 sits two minutes before the `FIRST_ENTRY_HOUR/MINUTE=16:32` gate
in `entry.py`; 23:05 is a few minutes past US regular-session close
(16:00 ET ≈ 23:00 Helsinki most of the year).

To change times or days, edit `/ST` and `/D` inside
`register_scheduled_tasks.bat` and rerun it. The script uses `/F`
(force overwrite), so re-running is idempotent.

## Smoke test

Run this once before you rely on the automation on a live trading day:

```
schtasks /Run /TN RiskEnv_Lock
notepad C:\codebase\env-repo\26_risk_manager.env
```

In Notepad, change any value and try Save. Windows should reject the
save with "Access is denied". Then unlock:

```
schtasks /Run /TN RiskEnv_Unlock
```

Save should now succeed. If both halves work, the automation is real.

## Uninstall

```
schtasks /Delete /TN RiskEnv_Lock   /F
schtasks /Delete /TN RiskEnv_Unlock /F
```

If a Deny ACE is still on the file after uninstalling, remove it
manually:

```
icacls C:\codebase\env-repo\26_risk_manager.env /remove:d "%USERNAME%"
```

## Gotchas

**Machine off at unlock time.** If the box is asleep or shut down at
23:05, the Deny ACE stays on the file overnight. The next morning's
lock task runs anyway (adds a redundant deny — harmless), but you
won't be able to edit the file until you either run
`unlock_risk_env.bat` manually or wait for the next 23:05.

If this matters, open Task Scheduler UI, edit `RiskEnv_Unlock`, and on
the *Settings* tab tick "Run task as soon as possible after a
scheduled start is missed". That flag can't be set via `schtasks.exe`.

**DST edge windows.** US enters DST in early March, Helsinki two weeks
later; US exits DST in early November, Helsinki a week earlier. During
those windows the offset shifts by an hour, so 09:30 ET moves to 15:30
Helsinki (from the usual 16:30). Your `FIRST_ENTRY_HOUR/MINUTE=16:32`
guard doesn't shift, so entries wait an hour anyway — nothing to
change on the freeze schedule for those weeks unless you also adjust
`FIRST_ENTRY_HOUR`.

**Adding a new risk key.** All twelve risk fields listed in
`backend/core/risk_manager_config.py` are required — pydantic refuses
to instantiate `RiskManagerSettings()` if any are missing, which will
abort FastAPI startup. When you add a key:

1. Add the typed field to `RiskManagerSettings`.
2. Add the `KEY=value` line to `26_risk_manager.env`.
3. Restart the FastAPI process (frozen singleton has no reload path).

**Running the app as a different Windows user.** The current setup
denies write to `%USERNAME%` — the user who executes the lock script.
If the FastAPI process runs as a different Windows user than the one
you interactively log in as (recommended: a dedicated `trader`
account), point the schtasks to run under that account with `/RU
trader`, and make sure your interactive user only has read permission
on `26_risk_manager.env`. That way changing the ACL requires a UAC
prompt from your interactive session — real friction under stress.

## Files in this folder

- `lock_risk_env.bat` — adds Deny ACE to `26_risk_manager.env`.
- `unlock_risk_env.bat` — removes the Deny ACE.
- `register_scheduled_tasks.bat` — one-shot Task Scheduler installer.
