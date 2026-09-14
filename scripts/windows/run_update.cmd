@echo off
REM Task Scheduler entry point. Waits for Docker, runs one refresh, keeps a log.
REM
REM   run_update.cmd --closing        hourly, buys only an imminent slate
REM   run_update.cmd --board          frequent, free
REM   run_update.cmd --daily odds     once a morning, full sync
REM   run_update.cmd --weekly         Tuesday, retrain
REM
REM Credits are only spent when the second argument is the word "odds", or in
REM --closing mode, which buys the slate about to kick off and nothing else.
REM
REM Docker Desktop has to be running. It is not started here on purpose: doing
REM it from a scheduled task launches the desktop UI on whatever session happens
REM to be active, and a refresh that quietly waits and then gives up is easier
REM to reason about than one that takes over the screen at eight in the morning.

setlocal enabledelayedexpansion
set "REPO=%~dp0..\.."
set "MODE=%~1"
if "%MODE%"=="" set "MODE=--board"

REM Second argument "odds" is what turns on spending. It is separate from the
REM mode so no run buys anything by accident: a task registered without it
REM cannot spend a credit whatever else changes.
set "ODDS=0"
if /I "%~2"=="odds" set "ODDS=1"

set "LOGDIR=%REPO%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-3 delims=/- " %%a in ("%DATE%") do set "STAMP=%%c%%a%%b"
set "LOG=%LOGDIR%\update-%STAMP%.log"

echo. >> "%LOG%"
echo ================================================== >> "%LOG%"
echo %DATE% %TIME%  starting %MODE% (odds=%ODDS%) >> "%LOG%"

REM Only one refresh at a time, across every mode.
REM
REM Task Scheduler's own setting stops a task overlapping itself and does
REM nothing about four different tasks. The weekly retrain runs about fifty
REM minutes from 01:00 and the hourly closing capture fires at 01:05, and
REM both rebuild prop_edges by truncating it, so an overlap is two jobs
REM emptying and refilling the same table at once.
REM
REM mkdir is atomic on Windows, so it works as a lock: the first run creates
REM the directory, later ones fail and wait. Up to thirty minutes, then give
REM up and say so in the log rather than running anyway.
set "LOCK=%REPO%\logs\.update.lock"

REM Clear a lock left behind by a run that never finished.
REM
REM Unlike flock on the server, a directory does not disappear when the
REM process holding it dies, and this machine sleeps. A lock left by a run
REM the laptop slept through would make every later run wait half an hour
REM and then skip, which reads exactly like the scheduler having stopped
REM working. Nothing here takes four hours, so an older lock is stale.
powershell -NoProfile -Command "$l='%LOCK%'; if (Test-Path $l) { if (((Get-Date) - (Get-Item $l).CreationTime).TotalHours -gt 4) { Remove-Item $l -Recurse -Force } }" >nul 2>&1
set /a WAITED=0
:acquire
mkdir "%LOCK%" >nul 2>&1
if errorlevel 1 (
  set /a WAITED+=1
  if !WAITED! GTR 180 (
    echo %DATE% %TIME%  another refresh still running after 30 min, skipping >> "%LOG%"
    endlocal & exit /b 0
  )
  timeout /t 10 /nobreak >nul
  goto acquire
)

REM Give Docker up to five minutes to come up after a reboot.
set /a TRIES=0
:waitdocker
docker info >nul 2>&1
if not errorlevel 1 goto haveDocker
set /a TRIES+=1
if %TRIES% GEQ 30 (
  echo %DATE% %TIME%  Docker never became available, giving up >> "%LOG%"
  rmdir "%LOCK%" >nul 2>&1
  exit /b 1
)
timeout /t 10 /nobreak >nul
goto waitdocker

:haveDocker
cd /d "%REPO%"
REM .env carries ADMIN_TOKEN and ODDS_API_KEY. Task Scheduler starts with a
REM bare environment, so the run has to read them itself or every write
REM endpoint comes back 401 and it fails at the first feature rebuild.
"C:\Program Files\Git\bin\bash.exe" -lc "set -a; . ./.env; set +a; ODDS=%ODDS% sh scripts/scheduled_update.sh %MODE%" >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo %DATE% %TIME%  finished %MODE% with exit code %RC% >> "%LOG%"

REM Released on every path out, including the one where Docker never
REM arrived. A lock left behind stops every later run for thirty minutes
REM apiece and then skips them, which would look exactly like the
REM scheduler having quietly stopped working.
rmdir "%LOCK%" >nul 2>&1
exit /b %RC%
