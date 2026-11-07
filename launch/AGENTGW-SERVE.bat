@echo off
setlocal
title AgentGW - tool-augmented gateway (8109, Peakstone Agent track)

REM ============================================================
REM  Tool-augmented gateway for the "Peakstone Agent" track (O-5).
REM  OpenAI-compatible proxy: bench talks to 8109, proxy runs a
REM  tool loop against the real model on 8107 with MCP tools
REM  (pyapi stdio + localdocs HTTP), 7 tool schemas, capped.
REM  REQUIRES: NanoPlus V3 alive on 8107 (START-NANOPLUS-V3.bat),
REM  localdocs server on 6280 (LOCALDOCS-SERVE.bat).
REM  NEVER bind 8107/8108 here (Hermes silent-swap trap).
REM  ASCII ONLY / CRLF. NO pause (runs under Task Scheduler).
REM ============================================================

"C:\Users\shogu\Strata-Hub\peakstone-venv\Scripts\python.exe" -X utf8 -I "C:\Users\shogu\Strata-Hub\agentgw\agentgw.py" >> "C:\Users\shogu\Strata-Hub\agentgw\agentgw-serve.log" 2>&1
echo === AGENTGW EXIT %ERRORLEVEL% %DATE% %TIME% === >> "C:\Users\shogu\Strata-Hub\agentgw\agentgw-serve.log"
