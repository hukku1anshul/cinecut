@echo off
chcp 65001 >nul
title CineCut AI - Intelligent Movie & Video Summarizer Studio

echo ======================================================================
echo   🎬 CineCut AI Studio 4.0 - Hollywood Story Editor & Viral Recap Engine
echo ======================================================================
echo.

cd /d "%~dp0"

:: Check if Python is available
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH!
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Launch CineCut server and browser interface
python run.py

if %errorlevel% neq 0 (
    echo.
    echo [!] CineCut server stopped with error code %errorlevel%.
    pause
)
