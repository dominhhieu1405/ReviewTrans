from __future__ import annotations

import threading


class ProviderError(RuntimeError):
    pass


class KeyRing:
    """Xoay vòng nhiều API key (tránh giới hạn quota)."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = [k.strip() for k in keys if k and k.strip()]
        self._index = 0
        self._lock = threading.Lock()

    def __bool__(self) -> bool:
        return bool(self.keys)

    def __len__(self) -> int:
        return len(self.keys)

    def current(self) -> str:
        with self._lock:
            return self.keys[self._index % len(self.keys)] if self.keys else ""

    def next(self) -> str:
        with self._lock:
            if not self.keys:
                return ""
            key = self.keys[self._index % len(self.keys)]
            self._index += 1
            return key
