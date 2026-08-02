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

python --version >nul 2>nul
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

python -c "import requests" >nul 2>nul
if errorlevel 1 (
    echo Build error: requests is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

python -c "from bs4 import BeautifulSoup" >nul 2>nul
if errorlevel 1 (
    echo Build error: BeautifulSoup is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

python -c "import trafilatura" >nul 2>nul
if errorlevel 1 (
    echo Build error: Trafilatura is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

python -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo Build error: Playwright is not available. Run: python -m pip install -r requirements.txt
    exit /b 1
)

if not exist "NEWS_SOURCE_MASTER.csv" (
    echo Build error: NEWS_SOURCE_MASTER.csv was not found.
    exit /b 1
)

if not exist "BACKUP_FEED_MASTER.csv" (
    echo Build error: BACKUP_FEED_MASTER.csv was not found.
    exit /b 1
)

if exist build rmdir /s /q build
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" >nul 2>nul
if errorlevel 1 (
    python -m playwright install chromium
    if errorlevel 1 (
        echo Build error: Playwright Chromium install failed.
        exit /b 1
    )
)

python scripts\stage_playwright_browsers.py --output build\playwright-browsers
if errorlevel 1 (
    echo Build error: Playwright browser staging failed.
    exit /b 1
)

python scripts\stage_tk_runtime.py --output build\tk-runtime
if errorlevel 1 (
    echo Build error: Tcl/Tk runtime staging failed.
    exit /b 1
)

for /f "usebackq delims=" %%A in (`python -c "import json; data=json.load(open('version.json', encoding='utf-8')); print(data.get('app_name') or '%FALLBACK_APP_NAME%')" 2^>nul`) do set "APP_NAME=%%A"
for /f "usebackq delims=" %%A in (`python -c "import json; data=json.load(open('version.json', encoding='utf-8')); print(data.get('updater_name') or '%FALLBACK_UPDATER_NAME%')" 2^>nul`) do set "UPDATER_NAME=%%A"
for /f "usebackq delims=" %%A in (`python -c "import sys; print(sys.base_prefix)" 2^>nul`) do set "PYTHON_BASE=%%A"

if "%APP_NAME%"=="" set "APP_NAME=%FALLBACK_APP_NAME%"
if "%UPDATER_NAME%"=="" set "UPDATER_NAME=%FALLBACK_UPDATER_NAME%"
if "%PYTHON_BASE%"=="" (
    echo Build error: Python base directory could not be resolved.
    exit /b 1
)

set "UPDATER_BASE=%UPDATER_NAME%"
if /I "%UPDATER_BASE:~-4%"==".exe" set "UPDATER_BASE=%UPDATER_BASE:~0,-4%"

set "ICON_ARGS="
if exist "assets\icon.ico" set "ICON_ARGS=--icon assets\icon.ico"

set "TRANSLATE_RULE_ARGS="
if exist "translate-rule.xlsx" set "TRANSLATE_RULE_ARGS=--add-data translate-rule.xlsx;."

if not exist dist mkdir dist
if exist "dist\%APP_NAME%.exe" del /q "dist\%APP_NAME%.exe"
if /I "%MODE%"=="first" if exist "dist\%UPDATER_NAME%" del /q "dist\%UPDATER_NAME%"

echo Building app: %APP_NAME%
python -m PyInstaller ^
--onefile ^
--windowed ^
--runtime-tmpdir temp ^
--add-data "version.json;." ^
--add-data "latest.json;." ^
--add-data "NEWS_SOURCE_MASTER.csv;." ^
--add-data "BACKUP_FEED_MASTER.csv;." ^
--add-data "build\playwright-browsers;playwright\driver\package\.local-browsers" ^
--add-data "%PYTHON_BASE%\Lib\tkinter;tkinter" ^
--add-data "build\tk-runtime\_tcl_data;_tcl_data" ^
--add-data "build\tk-runtime\_tk_data;_tk_data" ^
--add-binary "%PYTHON_BASE%\DLLs\_tkinter.pyd;." ^
--add-binary "%PYTHON_BASE%\DLLs\tcl86t.dll;." ^
--add-binary "%PYTHON_BASE%\DLLs\tk86t.dll;." ^
%TRANSLATE_RULE_ARGS% ^
--collect-all customtkinter ^
--collect-all playwright ^
--collect-all trafilatura ^
--collect-all bs4 ^
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
