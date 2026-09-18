@echo off
chcp 65001 >nul
title CineCut AI Studio - One-Click Automated Setup

echo ======================================================================
echo   🎬 CineCut AI Studio - Automated Zero-Friction Setup & Bootstrapper
echo ======================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python
echo [1/4] Checking Python environment...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [*] Python not detected. Attempting automatic installation via Windows Package Manager...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [!] Automatic installation failed. Please install Python from https://python.org
        pause
        exit /b 1
    )
    echo [✓] Python installed successfully.
) else (
    echo [✓] Python is available.
)

:: 2. Check FFmpeg
echo [2/4] Checking FFmpeg video engine...
where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo [*] FFmpeg not found in PATH. Attempting automatic installation via winget...
    winget install Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [!] Please ensure FFmpeg is installed and added to PATH.
    ) else (
        echo [✓] FFmpeg installed successfully.
    )
) else (
    echo [✓] FFmpeg is available.
)

:: 2b. Deno (JavaScript runtime that yt-dlp needs for YouTube downloads)
where deno >nul 2>nul
if %errorlevel% neq 0 (
    echo [*] Installing Deno for YouTube downloads...
    winget install DenoLand.Deno --silent --accept-package-agreements --accept-source-agreements
)

:: 3. Install Python Dependencies
echo [3/4] Verifying and updating Python dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -U -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [!] Some dependencies had warnings during install.
) else (
    echo [✓] Python dependencies verified.
)

:: 4. Generate Desktop Shortcut
echo [4/4] Generating Windows Desktop Shortcut...
python create_desktop_shortcut.py

echo.
echo ======================================================================
echo   🎉 Setup Complete! Launching CineCut AI Studio...
echo ======================================================================
echo.

call CineCut.bat
