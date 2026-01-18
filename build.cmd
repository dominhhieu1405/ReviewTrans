@echo off
setlocal

REM Build a standalone Windows executable (no external Python dependency).
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

if not exist bin\ffmpeg.exe (
  echo Missing bin\ffmpeg.exe. Place ffmpeg.exe, ffprobe.exe, and whisper.exe in bin\
  exit /b 1
)

python -m PyInstaller build.spec

echo Build complete: dist\VideoTranslationStudio\VideoTranslationStudio.exe
