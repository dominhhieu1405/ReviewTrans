# PyInstaller build spec for Video Translation Studio

from pathlib import Path

app_dir = Path(__file__).resolve().parent

block_cipher = None

a = Analysis(
    ["video_translator_app.py"],
    pathex=[str(app_dir)],
    binaries=[],
    datas=[
        (str(app_dir / "bin"), "bin"),
        (str(app_dir / "models"), "models"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="VideoTranslationStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="VideoTranslationStudio",
)

