"""Máy dịch không dùng ngữ cảnh: Google Translate và Microsoft Translator."""
from __future__ import annotations

import threading
import time

import requests

from ...context import apply_glossary_to_source
from ...langs import google_code, microsoft_code
from .. import ProviderError
from .base import TranslateJob, Translator

MAX_CHARS = 4500


def _chunks(lines: list[str], max_chars: int, max_items: int) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, line in enumerate(lines):
        length = len(line) + 1
        if current and (size + length > max_chars or len(current) >= max_items):
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        groups.append(current)
    return groups


class MachineTranslator(Translator):
    uses_context = False

    def translate(self, lines: list[str], job: TranslateJob) -> list[str]:
        prepared = [apply_glossary_to_source(line, job.glossary) for line in lines]
        results = [""] * len(lines)
        for group in _chunks(prepared, MAX_CHARS, self.max_batch):
            job.check_stop()
            texts = [prepared[i] for i in group]
            translated = self._retry(lambda: self._translate_many(texts, job), job)
            for i, text in zip(group, translated):
                results[i] = text
        return results

    def _retry(self, func, job: TranslateJob):
        last: Exception | None = None
        for attempt in range(4):
            job.check_stop()
            try:
                return func()
            except (requests.RequestException, ProviderError, ValueError, KeyError, IndexError) as exc:
                last = exc
                job.emit(f"  lỗi {type(exc).__name__}: {str(exc)[:200]} — thử lại ({attempt + 1}/4)")
                time.sleep(1.5 * (attempt + 1))
        raise ProviderError(f"{self.profile.name}: {last}")

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        raise NotImplementedError


class GoogleTranslator(MachineTranslator):
    """Có API key → Cloud Translation v2 (chính xác từng dòng). Không có → endpoint web miễn phí."""

    max_batch = 100

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        key = self.keys.next()
        target = google_code(job.target_lang)
        source = google_code(job.source_lang)
        if key:
            payload = {"q": texts, "target": target, "format": "text"}
            if source and source != "auto":
                payload["source"] = source
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": key},
                json=payload,
                timeout=60,
            )
            if response.status_code != 200:
                raise ProviderError(response.text[:300])
            return [item["translatedText"] for item in response.json()["data"]["translations"]]
        joined = "\n".join(t.replace("\n", " ") for t in texts)
        out = self._free(joined, source or "auto", target)
        parts = out.split("\n")
        if len(parts) == len(texts):
            return [p.strip() for p in parts]
        # lệch dòng → dịch từng dòng
        return [self._free(t, source or "auto", target).strip() for t in texts]

    @staticmethod
    def _free(text: str, source: str, target: str) -> str:
        response = requests.post(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": source, "tl": target, "dt": "t"},
            data={"q": text},
            timeout=60,
        )
        if response.status_code != 200:
            raise ProviderError(f"Google trả về {response.status_code}")
        data = response.json()
        return "".join(part[0] for part in data[0] if part and part[0])


class MicrosoftTranslator(MachineTranslator):
    """Có key → Azure Translator. Không có → token miễn phí của Microsoft Edge."""

    max_batch = 100
    _edge_token: str = ""
    _edge_expiry: float = 0.0
    _edge_lock = threading.Lock()

    def _auth_headers(self) -> dict:
        key = self.keys.next()
        if key:
            headers = {"Ocp-Apim-Subscription-Key": key}
            if self.profile.region:
                headers["Ocp-Apim-Subscription-Region"] = self.profile.region
            return headers
        with MicrosoftTranslator._edge_lock:
            if time.time() > MicrosoftTranslator._edge_expiry:
                response = requests.get("https://edge.microsoft.com/translate/auth", timeout=30)
                response.raise_for_status()
                MicrosoftTranslator._edge_token = response.text.strip()
                MicrosoftTranslator._edge_expiry = time.time() + 8 * 60
            return {"Authorization": f"Bearer {MicrosoftTranslator._edge_token}"}

    def _translate_many(self, texts: list[str], job: TranslateJob) -> list[str]:
        base = (self.profile.base_url or "https://api.cognitive.microsofttranslator.com").rstrip("/")
        params = {"api-version": "3.0", "to": microsoft_code(job.target_lang)}
        source = microsoft_code(job.source_lang)
        if source:
            params["from"] = source
        response = requests.post(
            f"{base}/translate",
            params=params,
            headers={**self._auth_headers(), "Content-Type": "application/json"},
            json=[{"Text": t} for t in texts],
            timeout=60,
        )
        if response.status_code == 401:
            MicrosoftTranslator._edge_expiry = 0
        if response.status_code != 200:
            raise ProviderError(f"Microsoft {response.status_code}: {response.text[:300]}")
        return [item["translations"][0]["text"] for item in response.json()]
