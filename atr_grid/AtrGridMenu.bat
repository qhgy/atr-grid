@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"

pushd "%PROJECT_ROOT%" >nul 2>nul
if errorlevel 1 (
    echo Failed to enter project root.
    pause
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found in PATH.
    echo Install uv first, then run this menu again.
    pause
    popd
    exit /b 1
)

if not defined PYSNOWBALL_PATH set "PYSNOWBALL_PATH=C:\Users\qhgy\Desktop\9527\pysnowball"
if not defined XUEQIU_COOKIE_FILE set "XUEQIU_COOKIE_FILE=D:\000000znb\1080\x1080x_attachments\xueqiu.com_cookies.txt"

:menu
cls
echo ==================================
echo           ATR GRID MENU
echo ==================================
echo 1. Generate plan
echo 2. Quick generate report
echo 3. Replay symbol
echo 4. Init paper portfolio
echo 5. Run paper day
echo 6. Show paper status
echo 7. Resume paper portfolio
echo 8. Exit
echo.
set "choice="
set /p "choice=Select an option [1-8]: "

if "%choice%"=="1" goto plan
if "%choice%"=="2" goto quick_report
if "%choice%"=="3" goto replay
if "%choice%"=="4" goto paper_init
if "%choice%"=="5" goto paper_run
if "%choice%"=="6" goto paper_status
if "%choice%"=="7" goto paper_resume
if "%choice%"=="8" goto end

echo.
echo Invalid selection.
call :pause_if_needed
goto menu

:plan
set "symbol="
set "shares="
set "save_report="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
set /p "shares=Enter reference shares [200]: "
if not defined shares set "shares=200"
set /p "save_report=Save JSON and Markdown report? [Y/n]: "
set "plan_args=plan %symbol% --shares %shares%"
if /I "%save_report%"=="N" set "plan_args=%plan_args% --no-save"
echo.
uv run python -m atr_grid %plan_args% <nul
echo.
call :pause_if_needed
goto menu

:quick_report
set "symbol="
set "shares="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
set /p "shares=Enter reference shares [200]: "
if not defined shares set "shares=200"
echo.
uv run python -m atr_grid plan %symbol% --shares %shares% <nul
echo.
call :pause_if_needed
goto menu

:replay
set "symbol="
set "shares="
set "lookback="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
set /p "shares=Enter reference shares [1000]: "
if not defined shares set "shares=1000"
set /p "lookback=Enter lookback days [60]: "
if not defined lookback set "lookback=60"
echo.
uv run python -m atr_grid replay %symbol% --lookback %lookback% --shares %shares% <nul
echo.
call :pause_if_needed
goto menu

:paper_init
set "symbol="
set "shares="
set "cash="
set "stop_price="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
set /p "shares=Enter initial shares [1000]: "
if not defined shares set "shares=1000"
set /p "cash=Enter initial cash [0]: "
if not defined cash set "cash=0"
set /p "stop_price=Enter stop price [blank to skip]: "
set "paper_init_args=python -m atr_grid.paper init %symbol% --shares %shares% --cash %cash%"
if defined stop_price set "paper_init_args=%paper_init_args% --stop-price %stop_price%"
echo.
uv run %paper_init_args% <nul
echo.
call :pause_if_needed
goto menu

:paper_run
set "symbol="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
echo.
uv run python -m atr_grid.paper run %symbol% <nul
echo.
call :pause_if_needed
goto menu

:paper_status
set "symbol="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
echo.
uv run python -m atr_grid.paper status %symbol% <nul
echo.
call :pause_if_needed
goto menu

:paper_resume
set "symbol="
set "stop_price="
set "clear_stop="
set /p "symbol=Enter symbol [SH515880]: "
if not defined symbol set "symbol=SH515880"
set /p "stop_price=Enter new stop price [blank to keep current]: "
set /p "clear_stop=Clear stop price? [y/N]: "
set "paper_resume_args=python -m atr_grid.paper resume %symbol%"
if defined stop_price set "paper_resume_args=%paper_resume_args% --stop-price %stop_price%"
if /I "%clear_stop%"=="Y" set "paper_resume_args=%paper_resume_args% --clear-stop"
echo.
uv run %paper_resume_args% <nul
echo.
call :pause_if_needed
goto menu

:end
popd
exit /b 0

:pause_if_needed
if "%NO_PAUSE%"=="1" exit /b 0
pause
exit /b 0
