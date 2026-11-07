@echo off
setlocal enabledelayedexpansion
title Qwen3.8-27B EfficientThink NanoPlus V3 (llama-server 8107) - local server

REM ============================================================
REM  Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3 (IsValorum,
REM  3.27 BPW, 11.18 GB): dense hybrid qwen35, vision via mmproj
REM  Q4_K_M, native ctx 262144. Uncensored finetune (EfficientThink).
REM
REM  REFUSAL PROBES 02.11 (8 probes): 7 COMPLY + 1 COMPLY+DISCLAIMER
REM  + 0 REFUSE. RUSSIAN IS CLEAN (unlike the pruned Flash-Next).
REM
REM  SPEED MODES (measured 02.11, same VRAM ~15.6-15.9 GiB):
REM    ctx 16384 + q8 KV + MTP n-max 3: 66-78 tok/s (fast, no Hermes)
REM    ctx 65536/131072 + draft-MTP   : 10-? tok/s  (NEVER: draft
REM                                     verify collapses on long KV)
REM    ctx 131072 + q4_0 KV, NO spec (THIS BAT): 54.9 tok/s, prompt 147.8
REM                                     ^ Hermes-compatible (>=64K)
REM  KV q4_0 halves the cache vs q8_0 (18 vs 36 KB/token) - it is
REM  what lets 128K fit at all; 256K does NOT fit even on q4_0
REM  (+2.4 GiB over budget). The draft-MTP head is from the BASE
REM  model - never use it at long context.
REM  Hermes needs n_ctx >= 64000; 131072 clears it with 2x margin.
REM
REM  Sampling: vendor APEX recipe from the card: temp 0.6
REM  (0.2-0.3 strict code), top-p 0.95, top-k 20, min-p 0.05,
REM  penalties OFF. reasoning_effort=low baked in; swaps below.
REM
REM  HERMES: provider "nanoplus-v3-efficientthink" in the
REM  pi-operator profile points at this port.
REM
REM  DO NOT RUN TOGETHER with the panel (8090), Cyber-Tiel
REM  (8099/8102), Flash-Next Coder (8106) or DeepSeek (8110):
REM  one 16 GB card holds ONE engine.
REM
REM  LIFETIME: the server lives as long as THIS window is open.
REM  ASCII ONLY on purpose. Keep this file plain ASCII / CRLF.
REM ============================================================

set "LLAMA=C:\Users\shogu\.unsloth\llama.cpp\build\bin\Release\llama-server.exe"
set "MDIR=C:\Users\shogu\Strata-Hub\models\Qwen3.8-27B-EfficientThink-NanoPlus-V3"
set "MODEL=%MDIR%\Qwen3.8-27B-EfficientThink-VAL-APEX-I-NanoPlus-V3.gguf"
set "MMPROJ=%MDIR%\mmproj-Qwen3.8-27B-Q4_K_M.gguf"
set "DFLASH=%MDIR%\speculative\dflash2-qwen38-27b-Q4_K_M.gguf"
set "PORT=8107"
set "URL=http://127.0.0.1:%PORT%"
set "LOG=C:\Users\shogu\Strata-Hub\launch\nanoplus-v3-server.log"

REM --- one card, one engine -------------------------------------------
netstat -ano | findstr ":8090 " | findstr "LISTENING" >nul && (
    echo   WARNING: START-PANEL is running on port 8090.
    pause
)
netstat -ano | findstr ":8099 " | findstr "LISTENING" >nul && (
    echo   WARNING: Cyber-Tiel MiniPlus is running on port 8099.
    pause
)
netstat -ano | findstr ":8102 " | findstr "LISTENING" >nul && (
    echo   WARNING: Cyber-Tiel NanoPlus is running on port 8102.
    pause
)
netstat -ano | findstr ":8106 " | findstr "LISTENING" >nul && (
    echo   WARNING: Flash-Next Coder - START-FLASHNEXT-CODER is running on 8106.
    pause
)
netstat -ano | findstr ":8110 " | findstr "LISTENING" >nul && (
    echo   WARNING: DeepSeek-V4.1-Flash is running on port 8110.
    pause
)

REM --- free the port ---------------------------------------------------
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo   freeing port %PORT% - PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

if not exist "%MODEL%" (
    echo   MODEL FILE MISSING: %MODEL%
    pause
    exit /b 1
)

REM --- free VRAM guard: ~11 GiB weights + 2.3 GiB KV q8 at 64K ----
set "VRAMFREE=0"
for /f "tokens=1,2 delims=, " %%a in ('"nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits"') do set /a VRAMFREE=%%b-%%a
echo   free VRAM: %VRAMFREE% MiB ^(want 12000+ for ctx 131072 (KV q4_0)^)
if %VRAMFREE% LSS 12000 goto vramlow
goto vramok
:vramlow
echo   NOT ENOUGH FREE VRAM. Waiting 20 s for pages to return, then
echo   retrying the check once...
ping -n 21 127.0.0.1 >nul
set "VRAMFREE=0"
for /f "tokens=1,2 delims=, " %%a in ('"nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits"') do set /a VRAMFREE=%%b-%%a
echo   free VRAM now: %VRAMFREE% MiB
if %VRAMFREE% LSS 12000 (
    echo   still not enough - exiting. Close the other engine window
    echo   and run again.
    exit /b 1
)
:vramok

echo   starting llama-server on %URL% ^(ctx 131072, single slot,
echo   NO speculative decoding - draft-MTP reverted 05.11: on 16GB
echo   it spilled VRAM and collapsed real workloads to 3.8 tok/s^)...

start "" /b cmd /c ""%LLAMA%" -m "%MODEL%" --mmproj "%MMPROJ%" --host 127.0.0.1 --port %PORT% -ngl 99 -c 131072 -np 1 -fa on -ctk q4_0 -ctv q4_0 --reasoning-budget 16384 --jinja --chat-template-kwargs "{\"reasoning_effort\":\"low\"}" --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.05 --threads 8 --metrics > "%LOG%" 2>&1"

REM --- variants --------------------------------------------------------
REM   FAST MODE (no Hermes, ctx 16384, +draft-MTP ~66-78 tok/s):
REM     swap "-c 65536 -np 1" for "-c 16384" and add:
REM       --spec-type draft-mtp --spec-draft-n-max 3
REM   NEVER combine 65536 with draft-MTP: measured 10.3 tok/s
REM   (draft verify collapses on the long KV; acceptance is fine
REM   at 0.75 - it is the verify pass that costs).
REM   DFlash2 external draft (untested): -md "%DFLASH%" --spec-draft-n-max 7
REM   reasoning swaps: {"reasoning_effort":"medium"|"xhigh"} or
REM                    {"enable_thinking":false}

REM --- wait for the server ---------------------------------------------
set /a tries=0
:wait
ping -n 4 127.0.0.1 >nul
curl -s -m 5 "%URL%/health" | findstr /c:"ok" >nul && goto ready
set /a tries+=1
if !tries! gtr 40 (
    echo   server did not come up in 2 minutes - tail of the log:
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 20"
    exit /b 1
)
goto wait

:ready
echo.
echo   ready: Qwen3.8-27B EfficientThink NanoPlus V3 on %URL%
echo   OpenAI API: %URL%/v1/chat/completions  ^(model name: any^)
echo   vision:     mmproj Q4_K_M attached - image_url messages work
echo   context:    65536, single slot, KV q8_0, -fa on, NO spec
echo   expected:   ~55 tok/s decode, ~148 tok/s prompt
echo   sampling:   APEX card recipe 0.6 / 0.95 / 20 / min-p 0.05
echo   thinking:   reasoning_effort=low baked in
echo   probes 02.11: 0 refusals out of 8; Russian clean
start "" "%URL%"
echo.
echo   the server runs while this window is open.
echo   to stop: close this window ^(Ctrl+C also works^).
echo.

:alive
REM NB: netstat lies inside the ZCode job tree (listener shows PID 0 or
REM vanishes) - liveness is /health, the same probe the wait loop uses.
ping -n 6 127.0.0.1 >nul
curl -s -m 5 "%URL%/health" | findstr /c:"ok" >nul && goto alive
echo.
echo   the server has stopped.
where nvidia-smi >nul 2>&1 && nvidia-smi --query-gpu=memory.used --format=csv,noheader
exit /b 0
