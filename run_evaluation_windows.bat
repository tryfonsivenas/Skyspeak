@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SkySpeak evaluation

rem ================================================================
rem  SETTINGS - change only these lines
rem ================================================================
set "OPENAI_MODEL=gpt-6.1-sol"
set "ANTHROPIC_MODEL=claude-sonnet-5-5"
set "LOCAL_MODELS=llama3.1:8b gemma3:27b qwen2.5:32b"
set "HARDWARE=RTX 4090"
rem ================================================================

echo.
echo  SkySpeak technical evaluation
echo  ------------------------------
echo  Runs everything that is still missing. Runs that are already complete are skipped.
echo  Leave this window open until it says FINISHED.
echo.

rem ---- 1. Python -------------------------------------------------
set "PY="
py -3.13 --version >nul 2>nul && set "PY=py -3.13"
if not defined PY ( py -3.12 --version >nul 2>nul && set "PY=py -3.12" )
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY ( py --version >nul 2>nul && set "PY=py" )
if not defined PY (
  echo  [!] Python was not found. Install it from python.org and tick "Add python.exe to PATH".
  pause & exit /b 1
)
echo  [1/7] Python found:
%PY% --version

rem ---- 2. Packages -----------------------------------------------
echo.
echo  [2/7] Checking packages...
%PY% -m pip install --quiet --disable-pip-version-check pyyaml httpx
if errorlevel 1 ( echo  [!] Package installation failed. & pause & exit /b 1 )
set "HAVE_PLOT=1"
%PY% -m pip install --quiet --disable-pip-version-check matplotlib
if errorlevel 1 (
  set "HAVE_PLOT="
  echo  [i] matplotlib could not be installed. Only the figure will be skipped.
)

rem ---- 3. Keys ---------------------------------------------------
echo.
echo  [3/7] API keys. Paste each key and press Enter (right-click pastes).
echo        Press Enter without a key to skip that provider. Keys are not saved anywhere.
if not defined ANTHROPIC_API_KEY set /p "ANTHROPIC_API_KEY=       Anthropic key: "
if not defined OPENAI_API_KEY set /p "OPENAI_API_KEY=       OpenAI key: "

set "COMMON=--hardware "%HARDWARE%" --skip-if-done"

rem ---- 4. Quick tests --------------------------------------------
echo.
echo  [4/7] Quick test of the cloud models (3 instructions each)
if defined ANTHROPIC_API_KEY (
  %PY% -m eval.run_eval --provider anthropic --model %ANTHROPIC_MODEL% --api-key-env ANTHROPIC_API_KEY --only A01,C01,E01 --repeats 1 --out eval\dokimes --fail-if-errors
  if errorlevel 1 ( echo. & echo  [!] Anthropic quick test failed. Read the error above. & pause & exit /b 1 )
)
if defined OPENAI_API_KEY (
  %PY% -m eval.run_eval --provider openai --model %OPENAI_MODEL% --api-key-env OPENAI_API_KEY --only A01,C01,E01 --repeats 1 --out eval\dokimes --fail-if-errors
  if errorlevel 1 ( echo. & echo  [!] OpenAI quick test failed. Read the error above. & pause & exit /b 1 )
)

rem ---- 5. Cloud runs: main set and set F, default and limits prompt
echo.
echo  [5/7] Cloud models: main set, set F, and both again with the classroom limits in the prompt
if defined ANTHROPIC_API_KEY (
  %PY% -m eval.run_eval --provider anthropic --model %ANTHROPIC_MODEL% --api-key-env ANTHROPIC_API_KEY %COMMON%
  %PY% -m eval.run_eval --provider anthropic --model %ANTHROPIC_MODEL% --api-key-env ANTHROPIC_API_KEY %COMMON% --testset eval\testset_f.json
  %PY% -m eval.run_eval --provider anthropic --model %ANTHROPIC_MODEL% --api-key-env ANTHROPIC_API_KEY %COMMON% --prompt-variant limits
  %PY% -m eval.run_eval --provider anthropic --model %ANTHROPIC_MODEL% --api-key-env ANTHROPIC_API_KEY %COMMON% --testset eval\testset_f.json --prompt-variant limits
)
if defined OPENAI_API_KEY (
  %PY% -m eval.run_eval --provider openai --model %OPENAI_MODEL% --api-key-env OPENAI_API_KEY %COMMON%
  %PY% -m eval.run_eval --provider openai --model %OPENAI_MODEL% --api-key-env OPENAI_API_KEY %COMMON% --testset eval\testset_f.json
  %PY% -m eval.run_eval --provider openai --model %OPENAI_MODEL% --api-key-env OPENAI_API_KEY %COMMON% --prompt-variant limits
  %PY% -m eval.run_eval --provider openai --model %OPENAI_MODEL% --api-key-env OPENAI_API_KEY %COMMON% --testset eval\testset_f.json --prompt-variant limits
)

rem ---- 6. Local models on the GPU --------------------------------
echo.
echo  [6/7] Local models through Ollama on the GPU: main set and set F
ollama --version >nul 2>nul
if errorlevel 1 (
  echo  [!] Ollama was not found in this window. Close it, open the file again, and the local runs will start.
  goto summary
)
for %%M in (%LOCAL_MODELS%) do (
  echo.
  echo  --- %%M ---
  ollama pull %%M
  if errorlevel 1 (
    echo  [!] Could not download %%M. Check the name on ollama.com/library. Skipping it.
  ) else (
    %PY% -m eval.run_eval --provider ollama --model %%M %COMMON%
    %PY% -m eval.run_eval --provider ollama --model %%M %COMMON% --testset eval\testset_f.json
    ollama stop %%M >nul 2>nul
  )
)

:summary
rem ---- 7. Tables, figure, zip ------------------------------------
echo.
echo  [7/7] Tables, figure and zip file
%PY% -m eval.summarize
%PY% -m eval.ablation
if defined HAVE_PLOT %PY% -m eval.plot_results
powershell -NoProfile -Command "Compress-Archive -Path 'eval\results\*' -DestinationPath 'results_for_paper.zip' -Force"
echo.
echo  ================================================================
echo   FINISHED. Send the file results_for_paper.zip (in this folder).
echo  ================================================================
pause
