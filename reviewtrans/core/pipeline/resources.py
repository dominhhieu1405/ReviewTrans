"""Tải và quản lý công cụ ngoài: ffmpeg, whisper.cpp, model whisper, libmpv."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import requests

from ..paths import find_libmpv, find_tool, models_dir, user_bin_dir
from ..proc import subprocess_kwargs

WHISPER_REPO = "ggerganov/whisper.cpp"
ProgressFn = Callable[[float, str], None]

KNOWN_WHISPER_MODELS = [
    "tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo",
    "small-q5_1", "medium-q5_0", "large-v3-turbo-q5_0",
]


def _noop(*_args) -> None:
    return None


def download_file(url: str, target: Path, progress: ProgressFn = _noop, stop=None, label: str = "") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "ReviewTrans"}) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with open(partial, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if stop is not None and stop.is_set():
                    raise InterruptedError("Đã huỷ tải")
                if chunk:
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        progress(done * 100.0 / total, f"{label} {done / 1e6:.1f}/{total / 1e6:.1f} MB")
    partial.replace(target)
    return target


def tool_status() -> dict[str, str]:
    status = {}
    for name in ("ffmpeg", "ffprobe", "whisper"):
        path = find_tool(name)
        status[name] = str(path) if path else ""
    mpv = find_libmpv()
    status["libmpv"] = str(mpv) if mpv else ""
    return status


# ------------------------------------------------------------------ ffmpeg


def download_ffmpeg(progress: ProgressFn = _noop, stop=None, bin_dir: Path | None = None) -> Path:
    system = platform.system().lower()
    if system.startswith("win"):
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        expected = ["ffmpeg.exe", "ffprobe.exe"]
    elif system.startswith("darwin"):
        url = "https://evermeet.cx/ffmpeg/getrelease/zip"
        expected = ["ffmpeg", "ffprobe"]
    else:
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"
        expected = ["ffmpeg", "ffprobe"]
    work = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
    try:
        archive = download_file(url, work / Path(url).name, progress, stop, "FFmpeg")
        progress(100, "Đang giải nén FFmpeg…")
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(work)
        else:
            shutil.unpack_archive(str(archive), str(work))
        bin_dir = bin_dir or user_bin_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in expected:
            matches = [p for p in work.rglob(name) if p.is_file()]
            if not matches:
                raise FileNotFoundError(f"Không thấy {name} trong gói tải về")
            shutil.copy2(matches[0], bin_dir / name)
            if not system.startswith("win"):
                (bin_dir / name).chmod(0o755)
        return bin_dir
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ whisper


def _github_asset(repo: str, predicate) -> tuple[str, str]:
    response = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=30)
    response.raise_for_status()
    for asset in response.json().get("assets", []):
        if predicate(asset["name"]):
            return asset["name"], asset["browser_download_url"]
    raise FileNotFoundError(f"Không tìm thấy bản build phù hợp trong {repo}")


def download_whisper_binaries(progress: ProgressFn = _noop, stop=None, bin_dir: Path | None = None) -> Path:
    if not platform.system().lower().startswith("win"):
        raise RuntimeError("Hãy cài whisper.cpp bằng trình quản lý gói và đặt whisper-cli vào thư mục bin.")
    name, url = _github_asset("ggml-org/whisper.cpp", lambda n: n == "whisper-bin-x64.zip")
    work = Path(tempfile.mkdtemp(prefix="whisper_dl_"))
    try:
        archive = download_file(url, work / name, progress, stop, "whisper.cpp")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(work)
        bin_dir = bin_dir or user_bin_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        for path in work.rglob("*"):
            if path.is_file() and path.suffix.lower() in (".exe", ".dll"):
                shutil.copy2(path, bin_dir / path.name)
        return bin_dir
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fetch_whisper_models() -> list[str]:
    response = requests.get(f"https://huggingface.co/api/models/{WHISPER_REPO}", timeout=30)
    response.raise_for_status()
    models = []
    for sibling in response.json().get("siblings", []):
        name = sibling.get("rfilename", "")
        if name.startswith("ggml-") and name.endswith(".bin"):
            models.append(name[len("ggml-"):-len(".bin")])
    return sorted(set(models))


def whisper_model_path(model: str) -> Path:
    return models_dir() / f"ggml-{model}.bin"


def downloaded_whisper_models() -> list[str]:
    return sorted(p.name[len("ggml-"):-len(".bin")] for p in models_dir().glob("ggml-*.bin"))


def ensure_whisper_model(model: str, progress: ProgressFn = _noop, stop=None) -> Path:
    path = whisper_model_path(model)
    if path.exists():
        return path
    url = f"https://huggingface.co/{WHISPER_REPO}/resolve/main/ggml-{model}.bin"
    return download_file(url, path, progress, stop, f"model {model}")


# ------------------------------------------------------------------ libmpv


def _seven_zip_exe() -> Path | None:
    found = shutil.which("7z") or shutil.which("7za")
    if found:
        return Path(found)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")):
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return Path(base) / "7-Zip" / "7z.exe"
    return None


def extract_7z(archive: Path, dest: Path) -> None:
    """Giải nén .7z. py7zr không hỗ trợ bộ lọc BCJ2 nên thử thêm 7-Zip và tar.exe (libarchive) của Windows."""
    errors: list[str] = []
    try:
        import py7zr

        with py7zr.SevenZipFile(archive, "r") as zf:
            zf.extractall(dest)
        return
    except Exception as exc:  # noqa: BLE001 - py7zr ném nhiều loại lỗi (BCJ2, định dạng...)
        errors.append(f"py7zr: {exc}")
    commands = []
    seven = _seven_zip_exe()
    if seven:
        commands.append([str(seven), "x", "-y", f"-o{dest}", str(archive)])
    system_tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if system_tar.is_file():
        commands.append([str(system_tar), "-xf", str(archive), "-C", str(dest)])
    for command in commands:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", check=False, **subprocess_kwargs(),
        )
        if result.returncode == 0:
            return
        errors.append(f"{Path(command[0]).name}: {result.stdout.strip()[-300:]}")
    raise RuntimeError(
        "Không giải nén được gói libmpv. Cài 7-Zip (7-zip.org) rồi thử lại, hoặc tự đặt libmpv-2.dll vào "
        f"{user_bin_dir()}.\n" + "\n".join(errors)
    )


def download_libmpv(progress: ProgressFn = _noop, stop=None, bin_dir: Path | None = None) -> Path:
    if not platform.system().lower().startswith("win"):
        raise RuntimeError("Hãy cài libmpv bằng trình quản lý gói của hệ điều hành.")
    name, url = _github_asset(
        "shinchiro/mpv-winbuild-cmake",
        lambda n: n.startswith("mpv-dev-x86_64-") and "-v3-" not in n and n.endswith(".7z"),
    )
    work = Path(tempfile.mkdtemp(prefix="mpv_dl_"))
    try:
        archive = download_file(url, work / name, progress, stop, "libmpv")
        progress(100, "Đang giải nén libmpv…")
        extracted = work / "out"
        extracted.mkdir()
        extract_7z(archive, extracted)
        dll = next((p for p in extracted.rglob("libmpv-2.dll")), None)
        if dll is None:
            raise FileNotFoundError("Không thấy libmpv-2.dll trong gói")
        target = (bin_dir or user_bin_dir()) / "libmpv-2.dll"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dll, target)
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)
