# Packaging Instructions (PyInstaller)

## 1. Install dependencies

```bash
pip install pyinstaller PyQt6 requests edge-tts openai google-genai
```

## 2. Folder layout

Ensure the following folders exist next to `main.py`:

```
bin/
  ffmpeg.exe
  ffprobe.exe
  whisper.exe
```

> On macOS/Linux, use the appropriate binaries (no `.exe`) in `bin/`.

## 3. Build command

```bash
pyinstaller build.spec
```

The output will be under `dist/VideoTranslationStudio`.
