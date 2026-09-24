from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from ..config import AppSettings
from ..models import Project
from ..proc import StopRequested
from ..store import ProjectStore


def _noop(*_args, **_kwargs) -> None:
    return None


@dataclass
class RunContext:
    store: ProjectStore
    project: Project
    settings: AppSettings
    log: Callable[[str], None] = _noop
    progress: Callable[[float, str], None] = _noop  # (0..100 của bước hiện tại, mô tả)
    video_changed: Callable[[str], None] = _noop  # báo UI tải lại video
    context_changed: Callable[[], None] = _noop
    stop_event: threading.Event = field(default_factory=threading.Event)

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested()
