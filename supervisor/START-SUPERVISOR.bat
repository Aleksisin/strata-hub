@echo off
setlocal
title SUPERVISOR v1 - register task and fire (past-date trigger, manual /Run)

REM ============================================================
REM  Registers StrataHub\SUPERVISOR (one-shot, date 01/01/2000 -
REM  never auto-fires, see 23:59 trigger storm lesson) and starts
REM  it via /Run. Stop: create state\stop.flag (graceful, finishes
REM  current run) or hard-kill the PID from state\supervisor.json.
REM  ASCII ONLY / CRLF. NO pause (Task Scheduler).
REM ============================================================

set "PY=C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe"
set "SUP=C:\Users\shogu\Strata-Hub\supervisor\supervisor.py"

schtasks /Create /F /TN "StrataHub\SUPERVISOR" /TR "\"%PY%\" -u \"%SUP%\"" /SC ONCE /SD 01/01/2000 /ST 00:00
if errorlevel 1 (
  echo REGISTER FAILED
  exit /b 1
)
REM /End clears a stale "running" instance record (hard-killed supervisor leaves
REM the task marked running, and /Run is then silently ignored)
schtasks /End /TN "StrataHub\SUPERVISOR" >nul 2>&1
schtasks /Run /TN "StrataHub\SUPERVISOR"
if errorlevel 1 (
  echo RUN FAILED
  exit /b 1
)
echo SUPERVISOR task fired - check state\supervisor.json heartbeat
exit /b 0
