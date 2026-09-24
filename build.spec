# PyInstaller spec cho ReviewTrans Studio — thường được gọi qua scripts/build.py
# Công cụ ngoài (ffmpeg, ffprobe, whisper, libmpv) lấy từ thư mục REVIEWTRANS_TOOLS (mặc định build/tools).
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

app_dir = Path(SPECPATH)
tools_dir = Path(os.environ.get("REVIEWTRANS_TOOLS", app_dir / "build" / "tools"))
version_file = os.environ.get("REVIEWTRANS_VERSION_FILE") or None

datas = [(str(app_dir / "icon.ico"), "."), (str(app_dir / "icon.png"), ".")]
if tools_dir.is_dir():
    # để nguyên file .exe/.dll (datas) — không cho PyInstaller phân tích/sửa
    datas += [(str(path), "bin") for path in sorted(tools_dir.iterdir()) if path.suffix.lower() in (".exe", ".dll")]

hiddenimports = (
    collect_submodules("reviewtrans")
    + collect_submodules("google.genai")
    + ["mpv", "edge_tts", "py7zr"]
)

a = Analysis(
    ["main.py"],
    pathex=[str(app_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "pyflakes", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReviewTrans",
    icon=str(app_dir / "icon.ico"),
    version=version_file,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX làm tăng cảnh báo nhầm của antivirus
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ReviewTrans",
)
