@echo off
setlocal
title AgentGW CONTROL - no local MCP (8109)

REM ============================================================
REM  Control-mode gateway: identical implementation (merge, SSE,
REM  pass-through, timeouts) but AGENTGW_NO_LOCAL=1 -> local MCP
%%  schemas are NOT offered; bench tools + pass-through only.
REM  Used by CHAIN-AGENT-CTL.bat for the isolation control run.
REM  ASCII ONLY / CRLF. NO pause (runs under Task Scheduler).
REM ============================================================

set "AGENTGW_NO_LOCAL=1"
"C:\Users\shogu\Strata-Hub\peakstone-venv\Scripts\python.exe" -X utf8 -I "C:\Users\shogu\Strata-Hub\agentgw\agentgw.py" >> "C:\Users\shogu\Strata-Hub\agentgw\agentgw-serve-ctl.log" 2>&1
echo === AGENTGW-CTL EXIT %ERRORLEVEL% %DATE% %TIME% === >> "C:\Users\shogu\Strata-Hub\agentgw\agentgw-serve-ctl.log"
