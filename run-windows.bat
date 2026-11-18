@echo off
rem Spuštění TickView na Windows (dvojklik v Průzkumníku nebo z příkazové řádky):
rem připraví .venv, doinstaluje závislosti při změně requirements.txt a spustí
rem aplikaci. Bez parametrů rovnou otevře prohlížeč (--open), jinak parametry
rem předá do main.py (např. run-windows.bat --port 8090 --open).

setlocal
rem Výpisy s diakritikou – konzole v UTF-8
chcp 65001 >nul

rem Pracujeme vždy v kořenu projektu, i když skript spustíme odjinud
cd /d "%~dp0"

set "VENV_PYTHON=.venv\Scripts\python.exe"

rem Virtuální prostředí vytvoříme jen při prvním spuštění
if exist "%VENV_PYTHON%" goto :venv_ready

rem Python hledáme přes launcher py (instalace z python.org), jinak python z PATH
set "PYTHON=python"
where py >nul 2>nul && set "PYTHON=py -3"

rem Kontrola verze 3.11+; selže i chybějící Python nebo alias z Microsoft Store
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Nenašel jsem Python 3.11 nebo novější.
    echo Nainstalujte ho z https://www.python.org/downloads/ ^(při instalaci zaškrtněte „Add python.exe to PATH“^) a spusťte skript znovu.
    goto :fail
)

echo Vytvářím virtuální prostředí .venv…
%PYTHON% -m venv .venv || goto :fail

:venv_ready
rem Závislosti se doinstalují jen při změně requirements.txt (společné s run.sh)
"%VENV_PYTHON%" tools\ensure_requirements.py || goto :fail

rem Bez parametrů (typicky dvojklik) otevřeme rovnou prohlížeč, jinak parametry beze změny
if "%~1"=="" (
    "%VENV_PYTHON%" main.py --open
) else (
    "%VENV_PYTHON%" main.py %*
)
if errorlevel 1 goto :fail
exit /b 0

:fail
rem Okno spuštěné dvojklikem se hned nezavře, aby šla přečíst chyba
echo.
echo Spuštění TickView selhalo.
pause
exit /b 1
