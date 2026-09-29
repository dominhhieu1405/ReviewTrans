"""Build ReviewTrans Studio: gom công cụ → PyInstaller (onedir) → tự kiểm tra → zip portable → installer.

Dùng chung cho build.cmd (máy cá nhân) và GitHub Actions.

    python scripts/build.py all [--version 2.1.0] [--no-libmpv] [--no-whisper] [--skip-installer]
    python scripts/build.py tools          # chỉ gom công cụ vào build/tools
    python scripts/build.py app|check|zip|installer
    python scripts/build.py whisper-vulkan # build whisper.cpp bản Vulkan (cần Vulkan SDK + Visual Studio)
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
WHISPER_CPP_REF = "v1.9.4"  # phiên bản whisper.cpp dùng để build bản Vulkan
VULKAN_ZIP = ROOT / "build" / "whisper-vulkan-x64.zip"
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


def fetch_tools(with_libmpv: bool = True, with_whisper: bool = True, vulkan_source: str = "") -> None:
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
            downloaded = exe.parent.name.startswith("whisper_")  # gói tải về chỉ chứa whisper → lấy mọi DLL
            shutil.copy2(exe, TOOLS / exe.name)
            for dll in exe.parent.glob("*.dll"):
                if downloaded or dll.name.lower().startswith(WHISPER_DLL_PREFIXES):
                    shutil.copy2(dll, TOOLS / dll.name)
        else:
            log("whisper.cpp: đã có")
    else:
        for name in WHISPER_EXES:
            (TOOLS / name).unlink(missing_ok=True)

    # whisper.cpp Vulkan (ASR bằng GPU AMD/Intel/NVIDIA) — tuỳ chọn, để trong thư mục con riêng
    if with_whisper:
        fetch_whisper_vulkan(vulkan_source)

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

    total = sum(p.stat().st_size for p in TOOLS.rglob("*") if p.is_file())
    log(f"Công cụ trong {TOOLS}: {', '.join(sorted(p.name for p in TOOLS.iterdir()))} ({total / 1e6:.0f} MB)")


def fetch_whisper_vulkan(source: str = "") -> None:
    """Đưa bản whisper.cpp Vulkan vào build/tools/whisper-vulkan.

    Nguồn theo thứ tự: tham số --whisper-vulkan (zip hoặc thư mục) → build/whisper-vulkan-x64.zip
    → bản đã có → tải từ release của ReviewTrans. Không có thì bỏ qua (app vẫn chạy whisper bằng CPU).
    """
    from reviewtrans.core.pipeline import resources

    target = TOOLS / "whisper-vulkan"
    candidate = Path(source) if source else (VULKAN_ZIP if VULKAN_ZIP.is_file() else None)
    if candidate is not None and candidate.exists():
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        if candidate.is_dir():
            for path in candidate.iterdir():
                if path.suffix.lower() in (".exe", ".dll"):
                    shutil.copy2(path, target / path.name)
        else:
            with zipfile.ZipFile(candidate) as zf:
                for info in zf.infolist():
                    name = Path(info.filename).name
                    if name.lower().endswith((".exe", ".dll")):
                        (target / name).write_bytes(zf.read(info))
        log(f"whisper.cpp Vulkan: lấy từ {candidate}")
    elif (target / "whisper-cli.exe").is_file():
        log("whisper.cpp Vulkan: đã có")
        return
    else:
        try:
            resources.download_whisper_vulkan(_progress("whisper Vulkan"), bin_dir=TOOLS)
            log("whisper.cpp Vulkan: đã tải từ release")
        except Exception as exc:  # noqa: BLE001 — chưa có release nào kèm bản Vulkan
            log(f"whisper.cpp Vulkan: bỏ qua ({exc})")
            return
    if not (target / "whisper-cli.exe").is_file():
        shutil.rmtree(target, ignore_errors=True)
        raise SystemExit("Gói whisper.cpp Vulkan không có whisper-cli.exe")


def build_whisper_vulkan(ref: str = WHISPER_CPP_REF) -> Path:
    """Build whisper-cli với backend Vulkan (liên kết tĩnh, CRT tĩnh) → build/whisper-vulkan-x64.zip.

    Cần: git, CMake, Visual Studio (C++), Vulkan SDK (biến VULKAN_SDK). whisper.cpp không phát hành
    bản Vulkan cho Windows nên ta tự build — CI chạy bước này rồi đính kèm file zip vào release.
    """
    if not os.environ.get("VULKAN_SDK"):
        raise SystemExit("Chưa có Vulkan SDK (biến môi trường VULKAN_SDK). Tải tại https://vulkan.lunarg.com/sdk/home")
    work = ROOT / "build" / "whisper-vulkan-src"
    if not (work / ".git").is_dir():
        shutil.rmtree(work, ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", ref,
                        "https://github.com/ggml-org/whisper.cpp.git", str(work)], check=True)
    out = work / "build-vulkan"
    configure = [
        "cmake", "-S", str(work), "-B", str(out),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",  # CRT tĩnh: khỏi cần VC++ Redistributable
        "-DBUILD_SHARED_LIBS=OFF",
        "-DGGML_VULKAN=ON",
        "-DGGML_NATIVE=OFF", "-DGGML_AVX2=ON", "-DGGML_FMA=ON", "-DGGML_F16C=ON",
        "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_SERVER=OFF", "-DWHISPER_SDL2=OFF", "-DWHISPER_CURL=OFF",
    ]
    log("whisper.cpp Vulkan: cấu hình CMake…")
    subprocess.run(configure, check=True)
    log("whisper.cpp Vulkan: đang build (biên dịch shader Vulkan mất vài phút)…")
    subprocess.run(["cmake", "--build", str(out), "--config", "Release", "--target", "whisper-cli",
                    "-j", str(os.cpu_count() or 4)], check=True)
    exe = next(out.rglob("whisper-cli.exe"), None)
    if exe is None:
        raise SystemExit("Build xong nhưng không thấy whisper-cli.exe")
    files = [exe] + sorted(exe.parent.glob("*.dll"))
    # kiểm tra chạy được (vulkan-1.dll đi kèm driver GPU / Vulkan SDK nên không đóng gói)
    check = subprocess.run([str(exe), "--help"], capture_output=True, text=True, errors="replace")
    if "--no-gpu" not in (check.stdout + check.stderr):
        if check.returncode & 0xFFFFFFFF == 0xC0000135:  # thiếu DLL: máy build không có vulkan-1.dll
            log("Cảnh báo: máy build thiếu vulkan-1.dll nên không chạy thử được whisper-cli")
        else:
            raise SystemExit(f"whisper-cli Vulkan không chạy được (mã {check.returncode & 0xFFFFFFFF:#x})")
    VULKAN_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(VULKAN_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.name)
        zf.writestr("VERSION.txt", f"whisper.cpp {ref} (GGML_VULKAN=ON)" + chr(10))
    log(f"whisper.cpp Vulkan: {VULKAN_ZIP} ({VULKAN_ZIP.stat().st_size / 1e6:.0f} MB, {', '.join(p.name for p in files)})")
    return VULKAN_ZIP


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
        run = data.get("runs", {}).get(tool)
        missing = "không có (tuỳ chọn)" if tool.startswith("whisper-") else "THIẾU"
        log(f"  {tool}: {path or missing}" + (f" (chạy thử: mã {run})" if run is not None else ""))
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
    parser.add_argument("step", nargs="?", default="all",
                        choices=["all", "tools", "app", "check", "zip", "installer", "whisper-vulkan"])
    parser.add_argument("--version", default="", help="mặc định: APP_VERSION trong reviewtrans/__init__.py")
    parser.add_argument("--no-libmpv", action="store_true", help="không đóng gói libmpv (nhẹ hơn ~120 MB)")
    parser.add_argument("--no-whisper", action="store_true", help="không đóng gói whisper.cpp")
    parser.add_argument("--whisper-vulkan", default="", help="zip/thư mục whisper.cpp Vulkan để đóng gói kèm")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--skip-zip", action="store_true")
    parser.add_argument("--require-installer", action="store_true", help="báo lỗi nếu không có Inno Setup (dùng trên CI)")
    args = parser.parse_args()

    version = args.version.strip().lstrip("v") or current_version()
    require = ["ffmpeg", "ffprobe"] + ([] if args.no_whisper else ["whisper"]) + ([] if args.no_libmpv else ["libmpv"])

    if args.step == "whisper-vulkan":
        build_whisper_vulkan()
        return
    if args.step in ("all", "tools"):
        fetch_tools(with_libmpv=not args.no_libmpv, with_whisper=not args.no_whisper, vulkan_source=args.whisper_vulkan)
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
