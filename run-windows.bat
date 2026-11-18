@echo off
rem Spuštění TickView na Windows (dvojklik v Průzkumníku nebo z příkazové řádky):
rem najde Python 3.12+ a když chybí, sám ho nainstaluje jen pro aktuálního
rem uživatele (winget, jinak instalátor z python.org), připraví .venv,
rem doinstaluje závislosti při změně requirements.txt a spustí aplikaci.
rem Bez parametrů rovnou otevře prohlížeč (--open), jinak parametry předá do
rem main.py (např. run-windows.bat --port 8090 --open).

setlocal
rem Výpisy s diakritikou – konzole v UTF-8
chcp 65001 >nul

rem Pracujeme vždy v kořenu projektu, i když skript spustíme odjinud
cd /d "%~dp0"

set "VENV_PYTHON=.venv\Scripts\python.exe"
rem Verze, která se nainstaluje, když žádný vhodný Python není (pandas-ta vyžaduje 3.12+);
rem balíček winget se odvodí z major.minor (3.13.12 → Python.Python.3.13)
set "PY_VERSION=3.13.12"
set "PY_WINGET_ID=Python.Python.%PY_VERSION:~0,4%"

rem Virtuální prostředí vytvoříme jen při prvním spuštění
if exist "%VENV_PYTHON%" goto :venv_ready

rem Hledání Pythonu, případně automatická instalace (ta na konci hledá znovu)
call :find_python
if not defined PYTHON (
    echo Nenašel jsem Python 3.12 nebo novější, instaluji Python %PY_VERSION% jen pro tohoto uživatele…
    call :install_python
)
if not defined PYTHON (
    echo Python se nepodařilo nainstalovat automaticky.
    echo Nainstalujte ho z https://www.python.org/downloads/ a spusťte skript znovu.
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


rem ---------- Podprogramy ----------

:find_python
rem Najde Python 3.12+ a příkaz uloží do PYTHON (nenalezen = prázdná proměnná).
rem Pořadí: launcher py, python z PATH, nakonec složka instalace pro uživatele
rem z python.org – hned po instalaci v tomto okně ještě není v PATH.
rem Alias python z Microsoft Store bez nainstalovaného Pythonu kontrolou neprojde.
rem Minimum 3.12 (výraz i poslední složka seznamu) odpovídá MIN_PYTHON
rem v tools\ensure_requirements.py.
set "PYTHON="
for %%C in ("py -3" "python") do if not defined PYTHON %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PYTHON=%%~C"
for %%V in (315 314 313 312) do if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYTHON="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe""
exit /b 0

:install_python
rem Instalace Pythonu jen pro aktuálního uživatele, bez administrátorských práv,
rem na konci vždy nové hledání. Nejdřív winget (součást Windows 11 i novějších
rem Windows 10); jeho výsledek ověří hledání, ne návratový kód.
where winget >nul 2>nul && winget install --exact --id %PY_WINGET_ID% --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
if defined PYTHON exit /b 0

rem Bez wingetu oficiální instalátor z python.org přes curl.exe, architektura podle procesoru
set "PY_ARCH=amd64"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PY_ARCH=arm64"
set "PY_INSTALLER=%TEMP%\python-%PY_VERSION%-%PY_ARCH%.exe"
echo Stahuji instalátor Pythonu z python.org…
curl.exe -L --fail -o "%PY_INSTALLER%" "https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-%PY_ARCH%.exe" || exit /b 1

rem Tichá instalace pro uživatele (i launcher py); PrependPath přidá Python do PATH pro příští okna
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0
del "%PY_INSTALLER%" >nul 2>nul
call :find_python
exit /b 0
