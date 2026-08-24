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
    """deep-translator GoogleTranslator with timeouts and a second Google web endpoint.

    deep-translator 1.11.4 scrapes Google's mobile HTML endpoint and does not pass
    an explicit timeout. That endpoint can also change its HTML or reject datacenter
    traffic. We keep the library's normal mobile endpoint as the first attempt and
    expose Google's public JSON web endpoint as a deferred fallback. TranslationService
    controls when that fallback is allowed so every provider request is rate-gated.

    Both endpoints are unofficial/public Google Translate endpoints. This improves
    resilience but does not provide an SLA. HTTP 429 is never bypassed with an extra
    request because doing so would make provider throttling worse.
    """

    _JSON_FALLBACK_URL = "https://translate.googleapis.com/translate_a/single"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }

    def __init__(self, *args, connect_timeout: float = 5.0, read_timeout: float = 15.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_timeout = (connect_timeout, read_timeout)

    def _get(self, url: str, *, params: dict) -> requests.Response:
        try:
            return requests.get(
                url,
                params=params,
                headers=self._HEADERS,
                proxies=self.proxies,
                timeout=self._request_timeout,
            )
        except requests.RequestException as exc:
            raise RequestError() from exc

    @staticmethod
    def _check_status(response: requests.Response, endpoint_name: str) -> None:
        if response.status_code == 429:
            raise TooManyRequests()
        if request_failed(status_code=response.status_code):
            # Do not log response bodies or source text. We only need the endpoint/status
            # to diagnose Render/provider failures without leaking clan chat content.
            log.warning("Google %s endpoint returned HTTP %s", endpoint_name, response.status_code)
            raise RequestError()

    @staticmethod
    def _same_text(text: str, translated: str) -> bool:
        source_alpha = "".join(ch for ch in text.strip() if ch.isalnum())
        translated_alpha = "".join(ch for ch in translated if ch.isalnum())
        return bool(source_alpha and translated_alpha and source_alpha == translated_alpha)

    def _translate_mobile(self, text: str) -> str:
        params = dict(self._url_params)
        params["tl"] = self._target
        params["sl"] = self._source
        if self.payload_key:
            params[self.payload_key] = text

        response = self._get(self._base_url, params=params)
        try:
            self._check_status(response, "mobile-html")
            soup = BeautifulSoup(response.text, "html.parser")
            element = soup.find(self._element_tag, self._element_query)
            if not element:
                element = soup.find(self._element_tag, self._alt_element_query)
            if not element:
                raise TranslationNotFound(text)

            translated = element.get_text(strip=True)
            if translated == text.strip() and self._same_text(text, translated):
                # Do not perform deep-translator's historical immediate second request
                # without `hl`. One logical attempt must equal one provider HTTP call so
                # the service-level rate gate remains authoritative. Some words/names
                # legitimately translate to themselves, so returning the echo is safe.
                return text.strip()

            return translated
        finally:
            response.close()

    def _translate_json(self, text: str) -> str:
        params = {
            "client": "gtx",
            "sl": self._source,
            "tl": self._target,
            "dt": "t",
            "q": text,
        }
        response = self._get(self._JSON_FALLBACK_URL, params=params)
        try:
            self._check_status(response, "json-fallback")
            try:
                payload = response.json()
            except ValueError as exc:
                raise TranslationNotFound(text) from exc

            segments = payload[0] if isinstance(payload, list) and payload else None
            if not isinstance(segments, list):
                raise TranslationNotFound(text)

            translated_parts: list[str] = []
            for segment in segments:
                if isinstance(segment, list) and segment and isinstance(segment[0], str):
                    translated_parts.append(segment[0])

            translated = "".join(translated_parts).strip()
            if not translated:
                raise TranslationNotFound(text)
            return translated
        finally:
            response.close()

    def translate_mobile(self, text: str) -> str:
        """Translate with the mobile HTML endpoint only.

        Provider retries/fallbacks are deliberately orchestrated by TranslationService
        so every HTTP request passes through the same global spacing/cooldown gate.
        This prevents one logical translation from silently creating two immediate
        Google requests.
        """
        if not is_input_valid(text, max_chars=5000):
            raise TranslationNotFound(text)

        text = text.strip()
        if self._same_source_target() or is_empty(text):
            return text
        return self._translate_mobile(text)

    def translate_json(self, text: str) -> str:
        """Translate with the JSON web endpoint only.

        This is a deferred fallback. TranslationService decides when it is safe to use
        it after a mobile endpoint/parser failure.
        """
        if not is_input_valid(text, max_chars=5000):
            raise TranslationNotFound(text)

        text = text.strip()
        if self._same_source_target() or is_empty(text):
            return text
        return self._translate_json(text)

    def translate(self, text: str, **kwargs) -> str:
        # Preserve GoogleTranslator-compatible behavior for direct callers while keeping
        # automatic fallback out of this method. TranslationService uses the explicit
        # endpoint methods above.
        return self.translate_mobile(text)


@dataclass(frozen=True)
class TranslationResult:
    text: str
    ok: bool
    attempts: int
    error: str | None = None
    rate_limit_errors: int = 0
    timeout_errors: int = 0


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
        fallback_delay_seconds: float,
    ) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._start_interval = start_interval_seconds
        self._retries = retries
        self._connect_timeout = connect_timeout_seconds
        self._read_timeout = read_timeout_seconds
        self._task_timeout = task_timeout_seconds
        self._cooldown_429 = cooldown_429_seconds
        self._fallback_delay = fallback_delay_seconds

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

    def _translate_sync(self, text: str, source: str, target: str, endpoint: str) -> str:
        translator, lock = self._translator_for(source, target)
        with lock:
            if endpoint == "json":
                return translator.translate_json(text)
            return translator.translate_mobile(text)

    @staticmethod
    def _is_rate_limit_error(exc: BaseException) -> bool:
        current: BaseException | None = exc
        while current is not None:
            if isinstance(current, TooManyRequests):
                return True
            message = f"{type(current).__name__}: {current}".lower()
            if "429" in message or "too many request" in message or "rate limit" in message:
                return True
            current = current.__cause__
        return False

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (asyncio.TimeoutError, requests.Timeout)):
                return True
            message = f"{type(current).__name__}: {current}".lower()
            if "timeout" in message or "timed out" in message:
                return True
            current = current.__cause__
        return False


    @staticmethod
    def _is_translation_not_found(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, TranslationNotFound):
                return True
            current = current.__cause__
        return False

    @staticmethod
    def _is_network_request_error(exc: BaseException) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, requests.RequestException):
                return True
            current = current.__cause__
        return False

    def _should_defer_to_json(self, exc: BaseException) -> bool:
        if self._is_rate_limit_error(exc) or self._is_network_request_error(exc):
            return False
        return self._is_translation_not_found(exc) or isinstance(exc, RequestError)

    @staticmethod
    def _describe_error(exc: BaseException | None) -> str:
        if exc is None:
            return "unknown"
        parts: list[str] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(parts) < 4:
            seen.add(id(current))
            message = str(current).strip()
            parts.append(f"{type(current).__name__}: {message}" if message else type(current).__name__)
            current = current.__cause__
        return " <- ".join(parts)

    async def translate(self, text: str, source: str, target: str) -> TranslationResult:
        if not text or not text.strip() or source == target:
            return TranslationResult(text=text, ok=True, attempts=0)

        protected = protect_text(text)
        last_error: BaseException | None = None
        rate_limit_errors = 0
        timeout_errors = 0
        endpoint = "mobile"

        for attempt in range(1, self._retries + 1):
            try:
                async with self._semaphore:
                    # Every provider HTTP call, including JSON fallback, must pass this
                    # gate. A 429 moves _cooldown_until forward, so all pending target
                    # languages stop starting new Google requests until the cooldown ends.
                    await self._wait_for_start_slot()
                    translated = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._translate_sync,
                            protected.text,
                            source,
                            target,
                            endpoint,
                        ),
                        timeout=self._task_timeout,
                    )
                if not translated or "<!DOCTYPE html>" in translated or "<html" in translated.lower():
                    raise TranslationNotFound(text)

                restored = restore_text(translated, protected.replacements)
                if attempt > 1:
                    log.info(
                        "Translation %s->%s recovered on attempt %d via %s endpoint",
                        source, target, attempt, endpoint,
                    )
                return TranslationResult(
                    text=restored,
                    ok=True,
                    attempts=attempt,
                    rate_limit_errors=rate_limit_errors,
                    timeout_errors=timeout_errors,
                )

            except Exception as exc:
                last_error = exc
                rate_limited = self._is_rate_limit_error(exc)
                if rate_limited:
                    rate_limit_errors += 1
                    await self._activate_429_cooldown()
                if self._is_timeout_error(exc):
                    timeout_errors += 1

                if attempt >= self._retries:
                    break

                if rate_limited:
                    # Do not add a contradictory 1-10 second retry timer. The next
                    # attempt will pass through _wait_for_start_slot(), which waits for
                    # the full global 429 cooldown. Keep the same endpoint after a 429.
                    log.warning(
                        "Translation %s->%s failed attempt %d/%d via %s: %s; "
                        "retry deferred until global %.0fs cooldown clears",
                        source, target, attempt, self._retries, endpoint,
                        self._describe_error(exc), self._cooldown_429,
                    )
                    continue

                if endpoint == "mobile" and self._should_defer_to_json(exc):
                    # TranslationNotFound can be Google's challenge/anti-bot HTML rather
                    # than a genuine language failure. Never hit the JSON endpoint
                    # immediately. Delay it, then let the normal request-start gate run.
                    endpoint = "json"
                    delay = self._fallback_delay
                    log.warning(
                        "Google mobile translation failed %s->%s (%s); "
                        "JSON fallback deferred %.1fs and will be rate-gated",
                        source, target, type(exc).__name__, delay,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue

                backoff = min(10.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                log.warning(
                    "Translation %s->%s failed attempt %d/%d via %s: %s; retrying in %.2fs",
                    source, target, attempt, self._retries, endpoint,
                    self._describe_error(exc), backoff,
                )
                await asyncio.sleep(backoff)

        error_name = type(last_error).__name__ if last_error else "unknown"
        log.error(
            "Translation %s->%s failed permanently after %d attempts: %s",
            source,
            target,
            self._retries,
            self._describe_error(last_error),
        )

        return TranslationResult(
            text=text,
            ok=False,
            attempts=self._retries,
            error=error_name,
            rate_limit_errors=rate_limit_errors,
            timeout_errors=timeout_errors,
        )

    def runtime_status(self) -> dict[str, float | int]:
        now = time.monotonic()
        return {
            "cooldown_remaining_seconds": max(0.0, self._cooldown_until - now),
            "next_start_in_seconds": max(0.0, self._next_start - now),
            "retries": self._retries,
            "fallback_delay_seconds": self._fallback_delay,
        }
