@echo off
setlocal enabledelayedexpansion

set "FALLBACK_APP_NAME=BV-App"
set "FALLBACK_UPDATER_NAME=BV-Updater.exe"
set "MODE=%~1"

if "%MODE%"=="" set "MODE=release"
if /I not "%MODE%"=="first" if /I not "%MODE%"=="release" (
    echo Usage: build.bat [first^|release]
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo Build error: python was not found in PATH.
    exit /b 1
)

python -c "import tkinter; import tkinter.ttk" >nul 2>nul
if errorlevel 1 (
    echo Build error: tkinter is not available.
    exit /b 1
)

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo Build error: PyInstaller is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

python -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo Build error: Playwright is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

set "PLAYWRIGHT_BROWSERS_PATH=0"
python -m playwright install chromium
if errorlevel 1 (
    echo Build error: Playwright Chromium install failed.
    exit /b 1
)

for /f "usebackq delims=" %%A in (`python -c "import json; data=json.load(open('version.json', encoding='utf-8')); print(data.get('app_name') or '%FALLBACK_APP_NAME%')" 2^>nul`) do set "APP_NAME=%%A"
for /f "usebackq delims=" %%A in (`python -c "import json; data=json.load(open('version.json', encoding='utf-8')); print(data.get('updater_name') or '%FALLBACK_UPDATER_NAME%')" 2^>nul`) do set "UPDATER_NAME=%%A"

if "%APP_NAME%"=="" set "APP_NAME=%FALLBACK_APP_NAME%"
if "%UPDATER_NAME%"=="" set "UPDATER_NAME=%FALLBACK_UPDATER_NAME%"

set "UPDATER_BASE=%UPDATER_NAME%"
if /I "%UPDATER_BASE:~-4%"==".exe" set "UPDATER_BASE=%UPDATER_BASE:~0,-4%"

set "ICON_ARGS="
if exist "assets\icon.ico" set "ICON_ARGS=--icon assets\icon.ico"

set "TRANSLATE_RULE_ARGS="
if exist "translate-rule.xlsx" set "TRANSLATE_RULE_ARGS=--add-data translate-rule.xlsx;."

if exist build rmdir /s /q build
if not exist dist mkdir dist
if exist "dist\%APP_NAME%.exe" del /q "dist\%APP_NAME%.exe"
if /I "%MODE%"=="first" if exist "dist\%UPDATER_NAME%" del /q "dist\%UPDATER_NAME%"

echo Building app: %APP_NAME%
python -m PyInstaller ^
--onefile ^
--windowed ^
--add-data "version.json;." ^
--add-data "latest.json;." ^
%TRANSLATE_RULE_ARGS% ^
--collect-all customtkinter ^
--collect-all playwright ^
--hidden-import tkinter ^
--hidden-import tkinter.ttk ^
%ICON_ARGS% ^
--name "%APP_NAME%" ^
app/main.py

if errorlevel 1 (
    echo Build error: app PyInstaller command failed.
    exit /b 1
)

if not exist "dist\%APP_NAME%.exe" (
    echo Build error: dist\%APP_NAME%.exe not found.
    exit /b 1
)

if /I "%MODE%"=="first" (
    echo Building updater: %UPDATER_NAME%
    python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "%UPDATER_BASE%" ^
    updater/updater.py

    if errorlevel 1 (
        echo Build error: updater PyInstaller command failed.
        exit /b 1
    )

    if not exist "dist\%UPDATER_NAME%" (
        echo Build error: dist\%UPDATER_NAME% not found.
        exit /b 1
    )
)

echo Build completed.
echo App: dist\%APP_NAME%.exe
if /I "%MODE%"=="first" echo Updater: dist\%UPDATER_NAME%
