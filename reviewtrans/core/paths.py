from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

USER_DIR_NAME = ".video_translation_studio"

IS_WINDOWS = platform.system().lower().startswith("win")

# Tên thay thế cho từng công cụ (whisper.cpp đổi tên main.exe -> whisper-cli.exe).
TOOL_ALIASES = {
    "whisper": ["whisper-cli", "whisper", "main"],
}


def app_root() -> Path:
    """Thư mục gốc chứa tài nguyên đóng gói (bin/, icon...)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def resource_path(relative: str) -> Path:
    return app_root() / relative


def exe_dir() -> Path:
    """Thư mục chứa file exe (bản đóng gói) hoặc thư mục mã nguồn."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    """Bản zip portable có file portable.txt cạnh exe → lưu mọi dữ liệu ngay trong thư mục app."""
    return bool(os.environ.get("REVIEWTRANS_PORTABLE")) or (
        getattr(sys, "frozen", False) and (exe_dir() / "portable.txt").is_file()
    )


def user_data_dir() -> Path:
    path = exe_dir() / "data" if is_portable() else Path.home() / USER_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_bin_dir() -> Path:
    """Nơi lưu các công cụ tải về (ffmpeg, whisper, libmpv)."""
    path = user_data_dir() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = user_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fonts_dir() -> Path:
    path = user_data_dir() / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_projects_root() -> Path:
    if is_portable():
        return user_data_dir() / "projects"
    return Path.home() / "ReviewTrans Projects"


def bin_dirs() -> list[Path]:
    dirs = [resource_path("bin"), resource_path("tools"), user_bin_dir()]
    # thư mục công cụ bổ sung (CI dùng build/tools); phân cách bằng os.pathsep
    dirs += [Path(p) for p in os.environ.get("REVIEWTRANS_EXTRA_BIN", "").split(os.pathsep) if p]
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "bin")
    return dirs


def _executable_names(name: str) -> list[str]:
    names = []
    for alias in TOOL_ALIASES.get(name, [name]):
        if IS_WINDOWS:
            names.append(f"{alias}.exe")
        names.append(alias)
    return names


def find_tool(name: str) -> Path | None:
    for directory in bin_dirs():
        for candidate in _executable_names(name):
            path = directory / candidate
            if path.is_file():
                return path
    return None


def find_libmpv() -> Path | None:
    names = ["libmpv-2.dll", "mpv-2.dll", "mpv-1.dll"] if IS_WINDOWS else ["libmpv.so.2", "libmpv.so", "libmpv.dylib"]
    for directory in bin_dirs():
        for name in names:
            path = directory / name
            if path.is_file():
                return path
    return None


def register_dll_dirs() -> None:
    """Thêm các thư mục bin vào PATH để ctypes/python-mpv tìm được DLL."""
    existing = os.environ.get("PATH", "")
    extra = [str(d) for d in bin_dirs() if d.is_dir() and str(d) not in existing]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + [existing])
    if IS_WINDOWS and hasattr(os, "add_dll_directory"):
        for directory in bin_dirs():
            if directory.is_dir():
                try:
                    os.add_dll_directory(str(directory))
                except OSError:
                    pass
