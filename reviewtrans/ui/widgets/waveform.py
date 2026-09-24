from __future__ import annotations

import array
import wave
from pathlib import Path


def load_peaks(path: Path, buckets_per_second: int = 20) -> tuple[list[float], float]:
    """Đọc WAV PCM16 mono/stereo, trả về (đỉnh 0..1 theo từng bucket, thời lượng)."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        width = wav.getsampwidth()
        frames = wav.getnframes()
        if width != 2:
            return [], frames / float(rate or 1)
        data = array.array("h")
        data.frombytes(wav.readframes(frames))
    if channels > 1:
        data = data[::channels]
    step = max(1, rate // buckets_per_second)
    peaks = []
    for start in range(0, len(data), step):
        chunk = data[start:start + step:4]
        if chunk:
            peaks.append(min(1.0, max(max(chunk), -min(chunk)) / 32768.0))
    top = max(peaks) if peaks else 1.0
    if top > 0:
        peaks = [p / top for p in peaks]
    return peaks, frames / float(rate or 1)
