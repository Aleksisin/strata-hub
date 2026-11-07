@echo off
title B8192-FULL (reasoning-budget 8192, all ten AgentBench tasks)
REM ============================================================
REM  Operator review 07.11: HOLD on production, GO on the full
REM  ten-task series on budget 8192 (unchanged model/engine/harness).
REM  The driver boots the 8192 server itself (freeze-abx5b parity
REM  + --metrics), queues ten tasks via the supervisor, waits for
REM  reports (8h cap) and restores prod via NP3-START.
REM  NEW PREFIX b8192f: the supervisor rejects duplicate run_id,
REM  so a re-run always needs a fresh prefix.
REM  ASCII ONLY / CRLF. Check args below before re-running.
REM ============================================================
set "B8192_PREFIX=b8192f"
set "B8192_LOG=chain-b8192f.log"
set "B8192_DEADLINE_H=8"
"C:\Users\shogu\AppData\Local\Programs\Python\Python314\python.exe" -I "C:\Users\shogu\strata_tournament\b8192_driver.py" d1-compression-casualties d2-clone-only-triage d3-adr-erattum bf1-budget-tree bf2-red-not-green bf3-expires-at bf4-gate-address bf5-range-key i1-guard-scan-window i2-tail-registry-synthetic >> "C:\Users\shogu\strata_tournament\b8192f-stdout.log" 2>&1
