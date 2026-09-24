from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from .models import (
    Project,
    ProjectContext,
    Segment,
    VideoDoc,
    VideoRef,
    from_dict,
    now,
    to_dict,
)

PROJECT_FILE = "project.json"
CONTEXT_FILE = "context.json"
VIDEO_FILE = "video.json"
SEGMENTS_FILE = "segments.json"

_write_lock = threading.RLock()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def slugify(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
    return cleaned or "Project"


class ProjectStore:
    """Đọc/ghi một thư mục project trên đĩa."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------ project

    @classmethod
    def create(cls, parent: Path, name: str) -> tuple["ProjectStore", Project]:
        parent = Path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        base = slugify(name)
        folder = parent / base
        counter = 2
        while folder.exists():
            folder = parent / f"{base} ({counter})"
            counter += 1
        folder.mkdir(parents=True)
        store = cls(folder)
        project = Project(name=name)
        store.save_project(project)
        store.save_context(ProjectContext())
        return store, project

    @classmethod
    def open(cls, path: str | Path) -> "ProjectStore":
        path = Path(path)
        if path.is_file():
            path = path.parent
        if not (path / PROJECT_FILE).exists():
            raise FileNotFoundError(f"Không phải thư mục project: {path}")
        return cls(path)

    @staticmethod
    def is_project_dir(path: Path) -> bool:
        return (Path(path) / PROJECT_FILE).is_file()

    def load_project(self) -> Project:
        return from_dict(Project, _read_json(self.root / PROJECT_FILE, {}))

    def save_project(self, project: Project) -> None:
        project.updated_at = now()
        _write_json(self.root / PROJECT_FILE, to_dict(project))

    def load_context(self) -> ProjectContext:
        return from_dict(ProjectContext, _read_json(self.root / CONTEXT_FILE, {}))

    def save_context(self, context: ProjectContext) -> None:
        _write_json(self.root / CONTEXT_FILE, to_dict(context))

    def output_dir(self, project: Project) -> Path:
        path = Path(project.output_dir) if project.output_dir else self.root / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------ videos

    def video_dir(self, video_id: str) -> Path:
        return self.root / "videos" / video_id

    def cache_dir(self, video_id: str) -> Path:
        path = self.video_dir(video_id) / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def tts_dir(self, video_id: str) -> Path:
        path = self.cache_dir(video_id) / "tts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load_video(self, video_id: str) -> VideoDoc:
        data = _read_json(self.video_dir(video_id) / VIDEO_FILE, None)
        if data is None:
            raise FileNotFoundError(f"Thiếu dữ liệu video {video_id}")
        return from_dict(VideoDoc, data)

    def save_video(self, doc: VideoDoc) -> None:
        doc.updated_at = now()
        _write_json(self.video_dir(doc.id) / VIDEO_FILE, to_dict(doc))

    def load_segments(self, video_id: str) -> list[Segment]:
        data = _read_json(self.video_dir(video_id) / SEGMENTS_FILE, [])
        return [from_dict(Segment, item) for item in data if isinstance(item, dict)]

    def save_segments(self, video_id: str, segments: list[Segment]) -> None:
        _write_json(self.video_dir(video_id) / SEGMENTS_FILE, [to_dict(s) for s in segments])

    def add_video(self, project: Project, source_path: str, info=None) -> VideoDoc:
        source = Path(source_path)
        doc = VideoDoc(name=source.stem, source_path=str(source))
        doc.apply_template(project.template)
        if info is not None:
            doc.duration = info.duration
            doc.width = info.width
            doc.height = info.height
            doc.fps = info.fps
            doc.has_audio = info.has_audio
        self.save_video(doc)
        self.save_segments(doc.id, [])
        project.videos.append(VideoRef(id=doc.id, name=doc.name, source_path=doc.source_path))
        self.save_project(project)
        return doc

    def remove_video(self, project: Project, video_id: str, delete_files: bool = True) -> None:
        project.videos = [v for v in project.videos if v.id != video_id]
        self.save_project(project)
        if delete_files:
            shutil.rmtree(self.video_dir(video_id), ignore_errors=True)

    def sync_ref(self, project: Project, doc: VideoDoc) -> None:
        for ref in project.videos:
            if ref.id == doc.id:
                ref.name = doc.name
                ref.source_path = doc.source_path

    def cache_size(self) -> int:
        total = 0
        videos = self.root / "videos"
        if videos.exists():
            for path in videos.rglob("*"):
                if path.is_file() and "cache" in path.parts:
                    total += path.stat().st_size
        return total

    def clear_cache(self, video_id: str | None = None, keep_tts: bool = True) -> None:
        targets = [self.video_dir(video_id)] if video_id else list((self.root / "videos").glob("*"))
        for vdir in targets:
            cache = vdir / "cache"
            if not cache.exists():
                continue
            for item in cache.iterdir():
                if keep_tts and item.name == "tts":
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
