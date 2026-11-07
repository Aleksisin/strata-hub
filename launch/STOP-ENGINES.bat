@echo off
setlocal enabledelayedexpansion
title Strata - stop ALL engine processes (free VRAM)

REM ============================================================
REM  Kills every engine process - any model, any port, any window:
REM    strata-poly.exe   (PolyStrata engine, all models)
REM    strata.exe        (upstream Strata engine)
REM    strata-vision.exe (vision encoder child)
REM    llama-server.exe  (Unsloth Studio llama.cpp - DeepSeek-V4.1-Flash)
REM  Serve layers (python) are NOT touched: an open panel window
REM  stays up but its model leaves the VRAM at once. NOTE: a panel
REM  window will LOAD THE MODEL AGAIN on the next request - close
REM  those windows too if the VRAM must stay free.
REM
REM  ASCII ONLY on purpose. Keep this file plain ASCII / CRLF.
REM ============================================================

echo   VRAM before:
where nvidia-smi >nul 2>&1 && nvidia-smi --query-gpu=memory.used --format=csv,noheader
echo.

set "FOUND=0"
for /f "skip=3 tokens=1" %%p in ('tasklist /FI "IMAGENAME eq strata-poly.exe"') do (
    if /i "%%p"=="strata-poly.exe" set "FOUND=1"
)
if "!FOUND!"=="1" (
    echo   killing strata-poly.exe ^(all instances^)...
    taskkill /IM strata-poly.exe /F >nul 2>&1
) else (
    echo   no strata-poly.exe running.
)

for /f "skip=3 tokens=1" %%p in ('tasklist /FI "IMAGENAME eq strata.exe"') do (
    if /i "%%p"=="strata.exe" (
        echo   killing strata.exe ^(upstream engine^)...
        taskkill /IM strata.exe /F >nul 2>&1
    )
)

for /f "skip=3 tokens=1" %%p in ('tasklist /FI "IMAGENAME eq strata-vision.exe"') do (
    if /i "%%p"=="strata-vision.exe" (
        echo   killing strata-vision.exe...
        taskkill /IM strata-vision.exe /F >nul 2>&1
    )
)

for /f "skip=3 tokens=1" %%p in ('tasklist /FI "IMAGENAME eq llama-server.exe"') do (
    if /i "%%p"=="llama-server.exe" (
        echo   killing llama-server.exe ^(DeepSeek-V4.1-Flash^)...
        taskkill /IM llama-server.exe /F >nul 2>&1
    )
)

echo.
echo   waiting 3 seconds for VRAM to settle...
timeout /t 3 /nobreak >nul
echo.
echo   VRAM after:
where nvidia-smi >nul 2>&1 && nvidia-smi --query-gpu=memory.used --format=csv,noheader
echo.
echo   reminder: an open panel window reloads its model on the next
echo   request - close those windows if the VRAM must stay free.
echo.
pause
