"""Build ReviewTrans Studio: gom công cụ → PyInstaller (onedir) → tự kiểm tra → zip portable → installer.

Dùng chung cho build.cmd (máy cá nhân) và GitHub Actions.

    python scripts/build.py all [--version 2.1.0] [--no-libmpv] [--no-whisper] [--skip-installer]
    python scripts/build.py tools          # chỉ gom công cụ vào build/tools
    python scripts/build.py app|check|zip|installer
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS = ROOT / "build" / "tools"
DIST = ROOT / "dist" / "ReviewTrans"
RELEASE = ROOT / "release"
EXE = DIST / "ReviewTrans.exe"
WHISPER_EXES = ("whisper-cli.exe", "whisper.exe", "main.exe")
WHISPER_DLL_PREFIXES = ("whisper", "ggml", "sdl2", "libgcc", "libstdc", "libwinpthread", "libgomp", "libomp", "msvcp", "vcruntime")


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def source_dirs() -> list[Path]:
    """Nơi tìm công cụ có sẵn trước khi phải tải: bin/ của repo và thư mục công cụ của app."""
    from reviewtrans.core.paths import user_bin_dir

    return [ROOT / "bin", user_bin_dir()]


def _find(name: str) -> Path | None:
    for folder in source_dirs():
        path = folder / name
        if path.is_file():
            return path
    return None


def _progress(label: str):
    state = {"last": -10.0}

    def report(pct: float, _msg: str = "") -> None:
        if pct - state["last"] >= 10 or pct >= 100:
            state["last"] = pct
            log(f"  {label}: {pct:.0f}%")

    return report


# ------------------------------------------------------------------ công cụ


def fetch_tools(with_libmpv: bool = True, with_whisper: bool = True) -> None:
    from reviewtrans.core.pipeline import resources

    TOOLS.mkdir(parents=True, exist_ok=True)

    # FFmpeg (bắt buộc)
    if not all((TOOLS / n).is_file() for n in ("ffmpeg.exe", "ffprobe.exe")):
        found = [_find(n) for n in ("ffmpeg.exe", "ffprobe.exe")]
        if all(found):
            for path in found:
                shutil.copy2(path, TOOLS / path.name)
            log(f"FFmpeg: dùng bản có sẵn ({found[0].parent})")
        else:
            log("FFmpeg: đang tải…")
            resources.download_ffmpeg(_progress("FFmpeg"), bin_dir=TOOLS)
    else:
        log("FFmpeg: đã có")

    # whisper.cpp (ASR)
    if with_whisper:
        have = next((TOOLS / n for n in WHISPER_EXES if (TOOLS / n).is_file()), None)
        if have is None:
            exe = next((p for p in (_find(n) for n in WHISPER_EXES) if p), None)
            if exe is None:
                log("whisper.cpp: đang tải…")
                staging = Path(tempfile.mkdtemp(prefix="whisper_"))
                resources.download_whisper_binaries(_progress("whisper.cpp"), bin_dir=staging)
                exe = next((staging / n for n in WHISPER_EXES if (staging / n).is_file()), None)
                if exe is None:
                    raise SystemExit("Gói whisper.cpp không có whisper-cli.exe")
            else:
                log(f"whisper.cpp: dùng bản có sẵn ({exe.parent})")
            shutil.copy2(exe, TOOLS / exe.name)
            for dll in exe.parent.glob("*.dll"):
                if dll.name.lower().startswith(WHISPER_DLL_PREFIXES):
                    shutil.copy2(dll, TOOLS / dll.name)
        else:
            log("whisper.cpp: đã có")
    else:
        for name in WHISPER_EXES:
            (TOOLS / name).unlink(missing_ok=True)

    # libmpv (player)
    target = TOOLS / "libmpv-2.dll"
    if with_libmpv:
        if not target.is_file():
            found = _find("libmpv-2.dll")
            if found:
                shutil.copy2(found, target)
                log(f"libmpv: dùng bản có sẵn ({found.parent})")
            else:
                log("libmpv: đang tải…")
                resources.download_libmpv(_progress("libmpv"), bin_dir=TOOLS)
        else:
            log("libmpv: đã có")
    else:
        target.unlink(missing_ok=True)

    total = sum(p.stat().st_size for p in TOOLS.iterdir() if p.is_file())
    log(f"Công cụ trong {TOOLS}: {', '.join(sorted(p.name for p in TOOLS.iterdir()))} ({total / 1e6:.0f} MB)")


# ------------------------------------------------------------------ phiên bản


def current_version() -> str:
    text = (ROOT / "reviewtrans" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("APP_VERSION = "):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


def write_version_files(version: str) -> Path:
    (ROOT / "reviewtrans" / "_build_info.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
    numbers = [int("".join(ch for ch in part if ch.isdigit()) or 0) for part in version.split("-")[0].split(".")]
    numbers = (numbers + [0, 0, 0, 0])[:4]
    tup = ", ".join(str(n) for n in numbers)
    info = ROOT / "build" / "version_info.txt"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'dominhhieu1405'),
      StringStruct('FileDescription', 'ReviewTrans Studio'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'ReviewTrans'),
      StringStruct('OriginalFilename', 'ReviewTrans.exe'),
      StringStruct('ProductName', 'ReviewTrans Studio'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return info


# ------------------------------------------------------------------ các bước


def build_app(version: str) -> None:
    if not (TOOLS / "ffmpeg.exe").is_file():
        raise SystemExit("Thiếu build/tools/ffmpeg.exe — chạy bước 'tools' trước.")
    info = write_version_files(version)
    env = dict(os.environ, REVIEWTRANS_TOOLS=str(TOOLS), REVIEWTRANS_VERSION_FILE=str(info))
    log(f"PyInstaller (phiên bản {version})…")
    started = time.time()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "build.spec")],
        cwd=ROOT, env=env, check=True,
    )
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    log(f"Xong sau {time.time() - started:.0f}s → {DIST} ({size / 1e6:.0f} MB)")


def self_check(require: list[str]) -> None:
    if not EXE.is_file():
        raise SystemExit(f"Không thấy {EXE}")
    report = ROOT / "build" / "selfcheck.json"
    report.unlink(missing_ok=True)
    log("Tự kiểm tra bản đóng gói…")
    # chạy trong thư mục tạm, không dùng PATH/thư mục công cụ của máy build để chắc exe tự đủ
    env = {k: v for k, v in os.environ.items() if k not in ("REVIEWTRANS_EXTRA_BIN",)}
    env["PATH"] = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"
    fake_home = Path(tempfile.mkdtemp(prefix="rt_home_"))  # không cho dùng công cụ trong thư mục người dùng
    env["USERPROFILE"] = env["HOME"] = str(fake_home)
    try:
        code = subprocess.run([str(EXE), "--self-check", str(report), *require], env=env, timeout=300).returncode
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    for name, state in data.get("modules", {}).items():
        if state != "ok":
            log(f"  ✕ {name}: {state}")
    log(f"  libmpv: {data.get('libmpv')}")
    for tool, path in data.get("tools", {}).items():
        log(f"  {tool}: {path or 'THIẾU'}")
    if code != 0 or not data.get("ok"):
        raise SystemExit("Tự kiểm tra thất bại — xem build/selfcheck.json")
    log("  ✓ đủ thư viện và công cụ")


def make_zip(version: str) -> Path:
    RELEASE.mkdir(exist_ok=True)
    target = RELEASE / f"ReviewTrans-{version}-portable.zip"
    target.unlink(missing_ok=True)
    log(f"Nén {target.name}…")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(DIST.rglob("*")):
            if path.is_file() and path.name != "portable.txt" and "data" not in path.relative_to(DIST).parts[:1]:
                zf.write(path, Path("ReviewTrans") / path.relative_to(DIST))
        zf.writestr(
            "ReviewTrans/portable.txt",
            "File này bật chế độ portable: cài đặt, project, model và công cụ tải thêm được lưu trong thư mục data\\ "
            "cạnh ReviewTrans.exe. Xoá file này để dùng thư mục người dùng (%USERPROFILE%\\.video_translation_studio).\n",
        )
    log(f"  → {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return target


def find_iscc() -> Path | None:
    candidates = [os.environ.get("ISCC", "")]
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        candidates.append(found)
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates += [str(Path(base) / "Inno Setup 6" / "ISCC.exe"), str(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe")]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def make_installer(version: str, required: bool) -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        message = "Không tìm thấy Inno Setup 6 (ISCC.exe) — bỏ qua installer. Cài tại https://jrsoftware.org/isdl.php"
        if required:
            raise SystemExit(message)
        log(message)
        return None
    RELEASE.mkdir(exist_ok=True)
    log(f"Inno Setup: {iscc}")
    subprocess.run(
        [str(iscc), "/Q", f"/DAppVersion={version}", f"/DSourceDir={DIST}", f"/DOutputDir={RELEASE}",
         str(ROOT / "installer" / "ReviewTrans.iss")],
        check=True,
    )
    target = RELEASE / f"ReviewTrans-{version}-setup.exe"
    log(f"  → {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return target


# ------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", nargs="?", default="all", choices=["all", "tools", "app", "check", "zip", "installer"])
    parser.add_argument("--version", default="", help="mặc định: APP_VERSION trong reviewtrans/__init__.py")
    parser.add_argument("--no-libmpv", action="store_true", help="không đóng gói libmpv (nhẹ hơn ~120 MB)")
    parser.add_argument("--no-whisper", action="store_true", help="không đóng gói whisper.cpp")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--skip-zip", action="store_true")
    parser.add_argument("--require-installer", action="store_true", help="báo lỗi nếu không có Inno Setup (dùng trên CI)")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v") or current_version()
    require = ["ffmpeg", "ffprobe"] + ([] if args.no_whisper else ["whisper"]) + ([] if args.no_libmpv else ["libmpv"])

    if args.step in ("all", "tools"):
        fetch_tools(with_libmpv=not args.no_libmpv, with_whisper=not args.no_whisper)
    if args.step in ("all", "app"):
        build_app(version)
    if args.step in ("all", "check"):
        self_check(require)
    if args.step in ("all", "zip") and not args.skip_zip:
        make_zip(version)
    if args.step in ("all", "installer") and not args.skip_installer:
        make_installer(version, args.require_installer)
    if args.step == "all":
        log(f"Hoàn tất. Kết quả trong {RELEASE}")


if __name__ == "__main__":
    main()
