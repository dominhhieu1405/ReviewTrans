from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import requests

from ...config import ProviderProfile
from ...proc import StopRequested
from .. import KeyRing, ProviderError

OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]


class TTSProvider:
    default_voice = ""

    def __init__(self, profile: ProviderProfile) -> None:
        self.profile = profile
        self.keys = KeyRing(profile.api_keys)

    @property
    def voice(self) -> str:
        return self.profile.option("voice", self.default_voice)

    def synthesize(
        self, text: str, voice: str, speed: float, out_base: Path, stop_event: threading.Event | None = None
    ) -> Path:
        """Tạo file âm thanh (đuôi tuỳ provider) tại out_base.<ext>, trả về đường dẫn."""
        raise NotImplementedError

    def list_voices(self, language: str = "") -> list[tuple[str, str]]:
        extra = [v.strip() for v in str(self.profile.option("voices", "")).split(",") if v.strip()]
        return [(v, v) for v in extra]

    def test(self, out_base: Path) -> Path:
        return self.synthesize("Xin chào, đây là giọng đọc thử.", self.voice, 1.0, out_base)


def _check_stop(stop_event: threading.Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise StopRequested()


def _download(url: str, target: Path) -> Path:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target


class EdgeTTS(TTSProvider):
    default_voice = "vi-VN-HoaiMyNeural"
    _voices_cache: list[dict] | None = None

    def synthesize(self, text, voice, speed, out_base, stop_event=None):
        import edge_tts

        _check_stop(stop_event)
        target = out_base.with_suffix(".mp3")
        rate = f"{int(round((speed - 1.0) * 100)):+d}%"
        pitch = f"{int(float(self.profile.option('pitch_hz', 0))):+d}Hz"

        async def _run():
            communicate = edge_tts.Communicate(text, voice or self.voice, rate=rate, pitch=pitch)
            await communicate.save(str(target))

        asyncio.run(_run())
        if not target.exists() or target.stat().st_size == 0:
            raise ProviderError("Edge TTS không trả về âm thanh")
        return target

    def list_voices(self, language: str = ""):
        import edge_tts

        if EdgeTTS._voices_cache is None:
            EdgeTTS._voices_cache = asyncio.run(edge_tts.list_voices())
        voices = []
        for item in EdgeTTS._voices_cache:
            locale = item.get("Locale", "")
            if language and not locale.lower().startswith(language.split("-")[0].lower()):
                continue
            label = f"{item.get('ShortName')} ({item.get('Gender', '')})"
            voices.append((item.get("ShortName", ""), label))
        return voices + super().list_voices(language)


class VbeeTTS(TTSProvider):
    def synthesize(self, text, voice, speed, out_base, stop_event=None):
        token = self.keys.next()
        app_id = self.profile.option("app_id", "")
        if not token or not app_id:
            raise ProviderError("vBee cần App ID và token")
        payload = {
            "app_id": app_id,
            "response_type": "indirect",
            "callback_url": self.profile.option("callback_url", "https://example.com/callback"),
            "input_text": text,
            "voice_code": voice or self.voice,
            "speed_rate": f"{max(0.1, min(1.9, speed)):.2f}",
        }
        base = (self.profile.base_url or "https://vbee.vn/api/v1").rstrip("/")
        response = requests.post(
            f"{base}/tts", json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != 1:
            raise ProviderError(f"vBee từ chối: {data.get('error_message') or data}")
        request_id = (data.get("result") or {}).get("request_id")
        if not request_id:
            raise ProviderError("vBee không trả request_id")
        polls = int(self.profile.option("max_polls", 30))
        for _ in range(polls):
            _check_stop(stop_event)
            time.sleep(1.0)
            poll = requests.get(f"{base}/tts/{request_id}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
            poll.raise_for_status()
            result = poll.json().get("result") or {}
            if result.get("status") == "FAILURE":
                raise ProviderError("vBee báo lỗi khi tạo âm thanh")
            link = result.get("audio_link")
            if link:
                ext = ".wav" if link.lower().split("?")[0].endswith(".wav") else ".mp3"
                return _download(link, out_base.with_suffix(ext))
        raise ProviderError("vBee: quá thời gian chờ")


class OpenAISpeechTTS(TTSProvider):
    """Mọi API tương thích POST {base}/audio/speech (OpenAI, các server TTS tự host...).

    Server tự host thường có giọng/model riêng: đọc từ GET {base}/voices và GET {base}/models.
    """

    AUDIO_FORMATS = ("wav", "mp3", "flac", "opus", "aac")
    _remote_cache: dict[tuple[str, str], tuple[float, list]] = {}
    _cache_lock = threading.Lock()

    @property
    def base(self) -> str:
        return (self.profile.base_url or "https://api.openai.com/v1").rstrip("/")

    @property
    def is_openai(self) -> bool:
        return "api.openai.com" in self.base

    @property
    def voice(self) -> str:
        return self.profile.option("voice", "alloy" if self.is_openai else "")

    def _headers(self) -> dict:
        key = self.keys.current()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _get_list(self, path: str) -> list:
        """GET {base}{path}, cache 10 phút. Trả về danh sách item (dict hoặc chuỗi)."""
        cache_key = (self.base, path)
        with OpenAISpeechTTS._cache_lock:
            cached = OpenAISpeechTTS._remote_cache.get(cache_key)
            if cached and time.time() - cached[0] < 600:
                return cached[1]
        response = requests.get(f"{self.base}{path}", headers=self._headers(), timeout=20)
        if response.status_code != 200:
            raise ProviderError(f"GET {path} → {response.status_code}")
        data = response.json()
        if isinstance(data, dict):
            data = data.get("data") or data.get("voices") or data.get("models") or []
        items = data if isinstance(data, list) else []
        with OpenAISpeechTTS._cache_lock:
            OpenAISpeechTTS._remote_cache[cache_key] = (time.time(), items)
        return items

    def remote_voices(self) -> list[tuple[str, str]]:
        voices = []
        for item in self._get_list("/voices"):
            if isinstance(item, str):
                voices.append((item, item))
            elif isinstance(item, dict):
                ident = str(item.get("id") or item.get("voice_id") or item.get("name") or "")
                if not ident:
                    continue
                name = str(item.get("name") or ident)
                desc = str(item.get("description") or item.get("gender") or "")
                voices.append((ident, f"{name} — {desc}" if desc else name))
        return voices

    def remote_models(self) -> list[str]:
        models = []
        for item in self._get_list("/models"):
            ident = item if isinstance(item, str) else (item.get("id") if isinstance(item, dict) else None)
            if ident:
                models.append(str(ident))
        return models

    def _model(self) -> str:
        if self.profile.model:
            return self.profile.model
        if self.is_openai:
            return "gpt-4o-mini-tts"
        try:
            models = self.remote_models()
        except (requests.RequestException, ProviderError, ValueError):
            models = []
        return models[0] if models else "tts-1"

    def _format(self) -> str:
        fmt = str(self.profile.option("format", "wav")).lower()
        return fmt if fmt in self.AUDIO_FORMATS else "wav"  # pcm thô không có header → xin wav

    def synthesize(self, text, voice, speed, out_base, stop_event=None):
        _check_stop(stop_event)
        voice = voice or self.voice
        if not voice:
            try:
                voices = self.remote_voices()
            except (requests.RequestException, ProviderError, ValueError):
                voices = []
            if not voices:
                raise ProviderError("Chưa chọn giọng — vào Providers bấm “Chọn giọng…”")
            voice = voices[0][0]
        fmt = self._format()
        payload = {
            "model": self._model(),
            "input": text,
            "voice": voice,
            "response_format": fmt,
            "speed": max(0.25, min(4.0, speed)),
        }
        instructions = self.profile.option("instructions", "")
        if instructions:
            payload["instructions"] = instructions
        headers = {"Content-Type": "application/json"}
        key = self.keys.next()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        response = requests.post(f"{self.base}/audio/speech", json=payload, headers=headers, timeout=180)
        if response.status_code != 200:
            raise ProviderError(f"TTS {response.status_code}: {response.text[:300]}")
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            raise ProviderError(f"TTS trả JSON thay vì âm thanh: {response.text[:300]}")
        target = out_base.with_suffix("." + fmt)
        target.write_bytes(response.content)
        return target

    def list_voices(self, language: str = ""):
        extra = super().list_voices(language)
        if self.is_openai:
            return [(v, v) for v in OPENAI_VOICES] + extra
        try:
            return self.remote_voices() + extra
        except (requests.RequestException, ProviderError, ValueError):
            return extra or [(v, v) for v in OPENAI_VOICES]


class LegacyCustomTTS(TTSProvider):
    """API cũ: POST form {text, language} -> {"code":0,"data":"<url>"}."""

    def synthesize(self, text, voice, speed, out_base, stop_event=None):
        if not self.profile.base_url:
            raise ProviderError("Thiếu URL API")
        headers = {}
        key = self.keys.next()
        if key:
            headers["Authorization"] = key
        payload = {"text": text, "language": voice or self.profile.option("language", "vi")}
        data = {}
        for attempt_text in (text, text + " @"):
            payload["text"] = attempt_text
            response = requests.post(self.profile.base_url, data=payload, headers=headers, timeout=60)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 9:
                break
        if data.get("code") != 0 or not data.get("data"):
            raise ProviderError(f"Custom API lỗi: {data.get('msg')}")
        return _download(data["data"], out_base.with_suffix(".mp3"))


_KINDS: dict[str, type[TTSProvider]] = {
    "edge": EdgeTTS,
    "vbee": VbeeTTS,
    "openai_speech": OpenAISpeechTTS,
    "legacy_custom": LegacyCustomTTS,
}


def create_tts(profile: ProviderProfile) -> TTSProvider:
    cls = _KINDS.get(profile.kind)
    if cls is None:
        raise ProviderError(f"Loại TTS không hỗ trợ: {profile.kind}")
    return cls(profile)
