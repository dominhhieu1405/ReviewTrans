@echo off
setlocal

REM Build a standalone Windows executable (no external Python dependency).
set "VENV_DIR=.venv_build"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  python -m venv "%VENV_DIR%"
)

set "PYTHON=%VENV_DIR%\Scripts\python.exe"

%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install -r requirements.txt pyinstaller

if not exist bin\ffmpeg.exe (
  echo Missing bin\ffmpeg.exe. Place ffmpeg.exe, ffprobe.exe, and whisper.exe in bin\
  exit /b 1
)

%PYTHON% -m PyInstaller build.spec

echo Build complete: dist\VideoTranslationStudio\VideoTranslationStudio.exe