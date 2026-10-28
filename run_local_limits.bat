@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SkySpeak local models with limits prompt
set "LOCAL_MODELS=llama3.1:8b gemma3:27b qwen2.5:32b"
set "PY="
py -3.13 --version >nul 2>nul && set "PY=py -3.13"
if not defined PY set "PY=python"
echo.
echo  Local models, main set and set F, with the classroom limits in the prompt.
echo  No keys needed. Completed runs are skipped.
echo.
for %%M in (%LOCAL_MODELS%) do (
  echo  --- %%M ---
  %PY% -m eval.run_eval --provider ollama --model %%M --hardware "RTX 4090" --skip-if-done --prompt-variant limits
  %PY% -m eval.run_eval --provider ollama --model %%M --hardware "RTX 4090" --skip-if-done --prompt-variant limits --testset eval\testset_f.json
  ollama stop %%M >nul 2>nul
)
%PY% -m eval.summarize
%PY% -m eval.ablation
%PY% -m eval.plot_results
powershell -NoProfile -Command "Compress-Archive -Path 'eval\results\*' -DestinationPath 'results_for_paper.zip' -Force"
echo.
echo  FINISHED. Send results_for_paper.zip again.
pause
