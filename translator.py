from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from deep_translator.exceptions import RequestError, TooManyRequests, TranslationNotFound
from deep_translator.validate import is_empty, is_input_valid, request_failed

from text_utils import protect_text, restore_text

log = logging.getLogger(__name__)


class TimeoutGoogleTranslator(GoogleTranslator):
    """deep-translator GoogleTranslator with explicit network timeouts.

    deep-translator 1.11.4 does not pass a timeout to requests.get(). Since the
    dependency is pinned, mirroring its small translate() implementation here is
    safer than allowing a provider connection to occupy a worker indefinitely.
    """

    def __init__(self, *args, connect_timeout: float = 5.0, read_timeout: float = 15.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_timeout = (connect_timeout, read_timeout)

    def translate(self, text: str, **kwargs) -> str:
        if is_input_valid(text, max_chars=5000):
            text = text.strip()
            if self._same_source_target() or is_empty(text):
                return text

            self._url_params["tl"] = self._target
            self._url_params["sl"] = self._source
            if self.payload_key:
                self._url_params[self.payload_key] = text

            try:
                response = requests.get(
                    self._base_url,
                    params=self._url_params,
                    proxies=self.proxies,
                    timeout=self._request_timeout,
                )
            except requests.RequestException as exc:
                raise RequestError() from exc

            try:
                if response.status_code == 429:
                    raise TooManyRequests()
                if request_failed(status_code=response.status_code):
                    raise RequestError()

                soup = BeautifulSoup(response.text, "html.parser")
                element = soup.find(self._element_tag, self._element_query)
                if not element:
                    element = soup.find(self._element_tag, self._alt_element_query)
                if not element:
                    raise TranslationNotFound(text)

                translated = element.get_text(strip=True)
                if translated == text.strip():
                    source_alpha = "".join(ch for ch in text.strip() if ch.isalnum())
                    translated_alpha = "".join(ch for ch in translated if ch.isalnum())
                    if source_alpha and translated_alpha and source_alpha == translated_alpha:
                        self._url_params["tl"] = self._target
                        if "hl" in self._url_params:
                            del self._url_params["hl"]
                            return self.translate(text)
                        return text.strip()
                return translated
            finally:
                response.close()


@dataclass(frozen=True)
class TranslationResult:
    text: str
    ok: bool
    attempts: int
    error: str | None = None


class TranslationService:
    def __init__(
        self,
        *,
        concurrency: int,
        start_interval_seconds: float,
        retries: int,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        task_timeout_seconds: float,
        cooldown_429_seconds: float,
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._start_interval = start_interval_seconds
        self._retries = retries
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._task_timeout = task_timeout_seconds
        self._cooldown_429 = cooldown_429_seconds

        self._rate_lock = asyncio.Lock()
        self._next_start = 0.0
        self._cooldown_until = 0.0

        self._pool_lock = threading.Lock()
        self._translators: dict[tuple[str, str], tuple[TimeoutGoogleTranslator, threading.Lock]] = {}

    async def _wait_for_start_slot(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait_until = max(self._next_start, self._cooldown_until)
            if wait_until > now:
                await asyncio.sleep(wait_until - now)
                now = time.monotonic()
            self._next_start = now + self._start_interval

    async def _activate_429_cooldown(self) -> None:
        async with self._rate_lock:
            until = time.monotonic() + self._cooldown_429
            if until > self._cooldown_until:
                self._cooldown_until = until
            log.warning("Google translation rate limit detected; global cooldown %.1fs", self._cooldown_429)

    def _translator_for(self, source: str, target: str) -> tuple[TimeoutGoogleTranslator, threading.Lock]:
        key = (source, target)
        with self._pool_lock:
            pair = self._translators.get(key)
            if pair is None:
                translator = TimeoutGoogleTranslator(
                    source=source,
                    target=target,
                    connect_timeout=self._connect_timeout,
                    read_timeout=self._read_timeout,
                )
                pair = (translator, threading.Lock())
                self._translators[key] = pair
            return pair

    def _translate_sync(self, text: str, source: str, target: str) -> str:
        translator, lock = self._translator_for(source, target)
        with lock:
            return translator.translate(text)

    @staticmethod
    def _is_rate_limit_error(exc: BaseException) -> bool:
        if isinstance(exc, TooManyRequests):
            return True
        message = f"{type(exc).__name__}: {exc}".lower()
        return "429" in message or "too many request" in message or "rate limit" in message

    async def translate(self, text: str, source: str, target: str) -> TranslationResult:
        if not text or not text.strip() or source == target:
            return TranslationResult(text=text, ok=True, attempts=0)

        protected = protect_text(text)
        last_error: BaseException | None = None

        for attempt in range(1, self._retries + 1):
            try:
                async with self._semaphore:
                    # Acquire concurrency capacity first, then reserve a provider start slot.
                    # This guarantees real HTTP starts remain staggered even when workers queue.
                    await self._wait_for_start_slot()
                    translated = await asyncio.wait_for(
                        asyncio.to_thread(self._translate_sync, protected.text, source, target),
                        timeout=self._task_timeout,
                    )
                if not translated or "<!DOCTYPE html>" in translated or "<html" in translated.lower():
                    raise TranslationNotFound(text)

                restored = restore_text(translated, protected.replacements)
                return TranslationResult(text=restored, ok=True, attempts=attempt)

            except Exception as exc:
                last_error = exc
                if self._is_rate_limit_error(exc):
                    await self._activate_429_cooldown()

                if attempt < self._retries:
                    backoff = min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                    log.warning(
                        "Translation %s->%s failed attempt %d/%d: %s; retrying in %.2fs",
                        source,
                        target,
                        attempt,
                        self._retries,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)

        error_name = type(last_error).__name__ if last_error else "unknown"
        log.error("Translation %s->%s failed permanently: %s", source, target, last_error)
        fallback = f"[Translation unavailable {source.upper()} -> {target.upper()}]\n{text}"
        return TranslationResult(text=fallback, ok=False, attempts=self._retries, error=error_name)
