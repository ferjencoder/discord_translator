from __future__ import annotations

import asyncio
import gc
import logging
import os
import time
from dataclasses import dataclass
import traceback
from argos_process import ArgosProcess, ArgosWorkerError
from check_argos_models import MissingArgosModel, MissingSentenceModel, sentence_model_path
from argos_config import active_languages, configure_argos, model_code
from text_utils import PROTECTED_PATTERN

log = logging.getLogger(__name__)


class ArgosBusy(RuntimeError):
    pass


def require_local_sentence_model(translation):
    """Resolve MiniSBD to an existing file so runtime cannot download a model."""
    from minisbd import SBDetect
    detector = translation.sentencizer
    detector.lang = str(sentence_model_path(detector))
    # Argos's default MiniSBD loader doesn't forward its thread settings.
    detector.detector = SBDetect(detector.lang, use_gpu=False, max_threads=1)


@dataclass(frozen=True)
class TranslationResult:
    text: str
    ok: bool
    attempts: int
    error: str | None = None
    rate_limit_errors: int = 0  # Compatibility with existing SQLite telemetry.
    timeout_errors: int = 0


class ArgosBackend:
    def __init__(self):
        configure_argos()
        self.languages = active_languages()
        self.low_memory = os.environ["ARGOS_LOW_MEMORY"].lower() in {"1", "true", "yes", "on"}
        self._cached = {}
        self._pivot = None

    def detect(self, text):
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        candidates = detect_langs(PROTECTED_PATTERN.sub(" ", text))
        best = candidates[0]
        lang = "no" if best.lang == "nb" else best.lang
        if best.prob < 0.80 or lang not in self.languages:
            raise ValueError("UnsupportedOrUncertainSourceLanguage")
        return lang

    def _leg(self, text, source, target):
        from argostranslate import package, translate
        logging.getLogger("argostranslate.utils").setLevel(logging.WARNING)
        pair = (model_code(source), model_code(target))
        translation = self._cached.get(pair)
        if translation is None:
            pkg = next((p for p in package.get_installed_packages()
                        if (p.from_code, p.to_code) == pair), None)
            if pkg is None:
                raise MissingArgosModel(f"Missing Argos model: {pair[0]}->{pair[1]}; run install_argos_models.py during build")
            # Avoid the global graph/cache: construct only the requested model.
            translation = translate.PackageTranslation(
                translate.Language(pkg.from_code, pkg.from_name),
                translate.Language(pkg.to_code, pkg.to_name), pkg)
            require_local_sentence_model(translation)
            if not self.low_memory:
                self._cached[pair] = translation
        try:
            return translation.translate(text)
        finally:
            if self.low_memory:
                if translation.translator is not None:
                    translation.translator.unload_model()
                    translation.translator = None
                del translation
                gc.collect()

    def translate(self, text, source, target):
        if source == "auto":
            source = self.detect(text)
        if source not in self.languages or target not in self.languages:
            raise ValueError("InactiveLanguage")
        if source == target:
            return text
        if source != "en":
            key = (text, source)
            if self._pivot is None or self._pivot[0] != key:
                self._pivot = (key, self._leg(text, source, "en"))
            text = self._pivot[1]
            if target == "en":
                return text
            source = "en"
        return self._leg(text, source, target)


class TranslationService:
    def __init__(self, *, concurrency, start_interval_seconds, retries,
                 task_timeout_seconds, backend=None):
        if concurrency != 1:
            raise ValueError("Local Argos requires TRANSLATION_CONCURRENCY=1")
        self._backend = backend if backend is not None else ArgosProcess()
        self._semaphore = asyncio.Semaphore(1)
        self._start_interval = start_interval_seconds
        self._retries = retries
        self._task_timeout = task_timeout_seconds
        self._next_start = 0.0
        self._running = None
        self._running_since = None
        self._last_result = None

    def _translate_sync(self, text, source, target):
        # Small neural models rewrite random placeholders. Keep protected spans
        # outside the model entirely, including during the English pivot.
        if source == "auto":
            source = self._backend.detect(text)
        chunks = []
        end = 0
        for match in PROTECTED_PATTERN.finditer(text):
            chunks.append(self._translate_plain(text[end:match.start()], source, target))
            chunks.append(match.group())
            end = match.end()
        chunks.append(self._translate_plain(text[end:], source, target))
        return "".join(chunks)

    def _translate_plain(self, text, source, target):
        if not any(c.isalpha() for c in text):
            return text
        result = self._backend.translate(text.strip(), source, target)
        if not result or not result.strip():
            raise ValueError("EmptyTranslation")
        return text[:len(text) - len(text.lstrip())] + result.strip() + text[len(text.rstrip()):]

    async def _run(self, text, source, target):
        async with self._semaphore:
            if self._running is not None and not self._running.done():
                # Native inference survives timeout/cancellation. Drop new work
                # until it ends rather than queueing more threads/models.
                raise ArgosBusy("Previous native inference is still running after timeout/cancellation")
            await asyncio.sleep(max(0, self._next_start - time.monotonic()))
            self._next_start = time.monotonic() + self._start_interval
            self._running_since = time.monotonic()
            if isinstance(self._backend, ArgosProcess):
                self._running = asyncio.create_task(self._backend.translate(text, source, target))
                return await asyncio.wait_for(self._running, self._task_timeout)
            self._running = asyncio.create_task(asyncio.to_thread(self._translate_sync, text, source, target))
            self._running.add_done_callback(self._consume_background_error)
            return await asyncio.wait_for(asyncio.shield(self._running), self._task_timeout)

    async def validate(self):
        if isinstance(self._backend, ArgosProcess):
            async with self._semaphore:
                return await asyncio.wait_for(self._backend.validate(), max(120, self._task_timeout))

    async def close(self):
        if isinstance(self._backend, ArgosProcess):
            await self._backend.close()

    @staticmethod
    def _consume_background_error(task):
        if not task.cancelled():
            task.exception()

    async def translate(self, text, source, target):
        if not text.strip() or source == target:
            return TranslationResult(text, True, 0)
        for attempt in range(1, self._retries + 1):
            try:
                translated = await self._run(text, source, target)
                self._last_result = {"ok": True, "source": source, "target": target, "error": None}
                return TranslationResult(translated, True, attempt)
            except asyncio.TimeoutError:
                self._last_result = {"ok": False, "source": source, "target": target, "error": "TimeoutError"}
                return TranslationResult("", False, attempt, "TimeoutError", timeout_errors=1)
            except Exception as exc:
                error = type(exc).__name__
                if isinstance(exc, (MissingArgosModel, MissingSentenceModel, ArgosBusy, ArgosWorkerError)):
                    detail = str(exc)  # Our messages contain only paths/codes, never chat.
                else:
                    # Stack locations aid diagnosis without logging third-party exception
                    # messages or frame locals, which can contain source chat text.
                    frames = traceback.extract_tb(exc.__traceback__)
                    detail = " > ".join(f"{frame.filename}:{frame.lineno} ({frame.name})"
                                        for frame in frames[-4:])
                log.warning("Local translation %s->%s failed (%s): %s", source, target, error, detail)
        self._last_result = {"ok": False, "source": source, "target": target, "error": error}
        return TranslationResult("", False, attempt, error)

    def runtime_status(self):
        busy = bool(self._running and not self._running.done())
        return {"provider": "Argos Translate (local CPU)",
                "cooldown_remaining_seconds": 0.0,
                "busy": busy,
                "worker_restarts": self._backend.restarts if isinstance(self._backend, ArgosProcess) else 0,
                "inference_elapsed_seconds": round(time.monotonic() - self._running_since, 1)
                if busy and self._running_since is not None else 0.0,
                "last_result": self._last_result}
