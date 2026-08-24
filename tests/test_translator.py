import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

try:
    import deep_translator  # noqa: F401
except ModuleNotFoundError:
    deep_translator = types.ModuleType("deep_translator")
    exceptions = types.ModuleType("deep_translator.exceptions")
    validate = types.ModuleType("deep_translator.validate")

    class GoogleTranslator:
        pass

    class RequestError(Exception):
        pass

    class TooManyRequests(Exception):
        pass

    class TranslationNotFound(Exception):
        pass

    deep_translator.GoogleTranslator = GoogleTranslator
    exceptions.RequestError = RequestError
    exceptions.TooManyRequests = TooManyRequests
    exceptions.TranslationNotFound = TranslationNotFound
    validate.is_empty = lambda text: not text
    validate.is_input_valid = lambda text, max_chars=5000: isinstance(text, str) and len(text) <= max_chars
    validate.request_failed = lambda status_code: status_code >= 400

    sys.modules["deep_translator"] = deep_translator
    sys.modules["deep_translator.exceptions"] = exceptions
    sys.modules["deep_translator.validate"] = validate

from deep_translator.exceptions import TooManyRequests, TranslationNotFound
from translator import TranslationService


class FakeTranslationService(TranslationService):
    def __init__(self, outcomes):
        super().__init__(
            concurrency=1,
            start_interval_seconds=1.5,
            retries=2,
            connect_timeout_seconds=1.0,
            read_timeout_seconds=1.0,
            task_timeout_seconds=2.0,
            cooldown_429_seconds=120.0,
            fallback_delay_seconds=10.0,
        )
        self.outcomes = list(outcomes)
        self.endpoints = []
        self.start_gate_calls = 0
        self.cooldown_calls = 0

    async def _wait_for_start_slot(self):
        self.start_gate_calls += 1

    async def _activate_429_cooldown(self):
        self.cooldown_calls += 1

    def _translate_sync(self, text, source, target, endpoint):
        self.endpoints.append(endpoint)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class TranslationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_not_found_defers_json_fallback(self):
        service = FakeTranslationService([
            TranslationNotFound("hello"),
            "hallo",
        ])

        with patch("translator.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await service.translate("hello", "en", "de")

        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(service.endpoints, ["mobile", "json"])
        self.assertEqual(service.start_gate_calls, 2)
        sleep_mock.assert_awaited_once_with(10.0)

    async def test_429_retries_after_global_gate_without_short_backoff(self):
        service = FakeTranslationService([
            TooManyRequests(),
            "hallo",
        ])

        with patch("translator.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await service.translate("hello", "en", "de")

        self.assertTrue(result.ok)
        self.assertEqual(result.rate_limit_errors, 1)
        self.assertEqual(service.cooldown_calls, 1)
        self.assertEqual(service.endpoints, ["mobile", "mobile"])
        self.assertEqual(service.start_gate_calls, 2)
        sleep_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
