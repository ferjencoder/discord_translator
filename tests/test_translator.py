import asyncio
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from translator import ArgosBackend, TranslationResult, TranslationService


def service(backend, **kwargs):
    return TranslationService(concurrency=1, start_interval_seconds=0,
                              retries=kwargs.pop("retries", 1),
                              task_timeout_seconds=kwargs.pop("timeout", 2),
                              backend=backend, **kwargs)


class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_preserves_protected_spans_and_whitespace(self):
        backend = Mock()
        backend.translate.side_effect = lambda text, source, target: text.upper()
        result = await service(backend).translate(
            "hello <@123> see https://example.com/x and `hello` K:1 X:2 Y:3", "en", "es")
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "HELLO <@123> SEE https://example.com/x AND `hello` K:1 X:2 Y:3")
        for call in backend.translate.call_args_list:
            self.assertNotIn("https://", call.args[0])
            self.assertNotIn("<@", call.args[0])

    async def test_failed_and_empty_translations_never_return_original(self):
        for outcome in (RuntimeError("private source content"), ""):
            backend = Mock()
            if isinstance(outcome, Exception):
                backend.translate.side_effect = outcome
            else:
                backend.translate.return_value = outcome
            result = await service(backend).translate("secret", "en", "es")
            self.assertFalse(result.ok)
            self.assertEqual(result.text, "")
            self.assertNotIn("private", result.error)

    async def test_retry_can_recover(self):
        backend = Mock()
        backend.translate.side_effect = [ValueError(), "hola"]
        result = await service(backend, retries=2).translate("hello", "en", "es")
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)

    async def test_identity_and_attachment_only_do_not_load_model(self):
        backend = Mock()
        translator = service(backend)
        for text, source, target in (("", "en", "es"), ("hello", "en", "en"),
                                     ("https://example.com", "en", "es")):
            result = await translator.translate(text, source, target)
            self.assertTrue(result.ok)
            self.assertEqual(result.text, text)
        backend.translate.assert_not_called()

    async def test_reaction_detects_original_once(self):
        backend = Mock()
        backend.detect.return_value = "es"
        backend.translate.return_value = "hello"
        result = await service(backend).translate("hola <@123>", "auto", "en")
        self.assertTrue(result.ok)
        backend.detect.assert_called_once_with("hola <@123>")
        backend.translate.assert_called_once_with("hola", "es", "en")

    async def test_timeout_does_not_start_overlapping_native_inference(self):
        release = threading.Event()
        started = threading.Event()
        backend = Mock()
        def blocking(*args):
            started.set()
            release.wait(3)
            return "hola"
        backend.translate.side_effect = blocking
        translator = service(backend, timeout=0.05)
        try:
            result = await translator.translate("hello", "en", "es")
            self.assertTrue(started.is_set())
            self.assertEqual(result.timeout_errors, 1)
            self.assertEqual(result.text, "")
            second = await translator.translate("hello", "en", "de")
            self.assertFalse(second.ok)
            self.assertEqual(backend.translate.call_count, 1)
        finally:
            release.set()
            await translator._running
        backend.translate.side_effect = None
        backend.translate.return_value = "hallo"
        self.assertTrue((await translator.translate("hello", "en", "de")).ok)

    async def test_cancelled_request_does_not_overlap_native_inference(self):
        release = threading.Event()
        started = threading.Event()
        backend = Mock()
        def blocking(*args):
            started.set()
            release.wait(3)
            return "hola"
        backend.translate.side_effect = blocking
        translator = service(backend)
        task = asyncio.create_task(translator.translate("hello", "en", "es"))
        try:
            while not started.is_set():
                await asyncio.sleep(0.005)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse((await translator.translate("hello", "en", "de")).ok)
            self.assertEqual(backend.translate.call_count, 1)
        finally:
            release.set()
            await translator._running


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"ARGOS_LOW_MEMORY": "true",
                                          "ACTIVE_TRANSLATION_LANGS": "en,es,ar,de,fr,no,pt"})
        self.env.start()
        self.addCleanup(self.env.stop)
        from argos_config import configure_argos
        configure_argos()

    def test_pivot_releases_first_leg_before_second(self):
        backend = ArgosBackend()
        backend._leg = Mock(side_effect=["hello", "hallo"])
        self.assertEqual(backend.translate("hola", "es", "de"), "hallo")
        self.assertEqual([c.args for c in backend._leg.call_args_list],
                         [("hola", "es", "en"), ("hello", "en", "de")])

    def test_detection_rejects_unsupported_and_uncertain(self):
        backend = ArgosBackend()
        for code, probability in (("ru", 0.99), ("es", 0.7)):
            with patch("langdetect.detect_langs", return_value=[SimpleNamespace(lang=code, prob=probability)]):
                with self.assertRaises(ValueError):
                    backend.detect("text")

    def test_norwegian_pair_and_cleanup_even_on_failure(self):
        pkg = SimpleNamespace(from_code="en", to_code="nb", from_name="English", to_name="Norwegian")
        for outcome in ("hei", RuntimeError("failure")):
            translation = Mock()
            native = translation.translator
            translation.translate.side_effect = outcome if isinstance(outcome, Exception) else None
            translation.translate.return_value = outcome
            with patch("argostranslate.package.get_installed_packages", return_value=[pkg]), patch(
                "argostranslate.translate.PackageTranslation", return_value=translation), patch(
                "translator.require_local_sentence_model"):
                backend = ArgosBackend()
                if isinstance(outcome, Exception):
                    with self.assertRaises(RuntimeError):
                        backend.translate("hello", "en", "no")
                else:
                    self.assertEqual(backend.translate("hello", "en", "no"), "hei")
                native.unload_model.assert_called_once()
                self.assertIsNone(translation.translator)
                self.assertEqual(backend._cached, {})

    def test_missing_model_has_no_download_fallback(self):
        with patch("argostranslate.package.get_installed_packages", return_value=[]), patch(
            "argostranslate.package.update_package_index") as update:
            with self.assertRaises(RuntimeError):
                ArgosBackend().translate("hello", "en", "es")
            update.assert_not_called()

    def test_disabled_language_fails(self):
        with self.assertRaises(ValueError):
            ArgosBackend().translate("hello", "en", "ru")

    def test_missing_sentence_model_is_rejected_without_download(self):
        from translator import require_local_sentence_model
        translation = SimpleNamespace(sentencizer=SimpleNamespace(lang="en"))
        with patch("translator.Path.is_file", return_value=False), patch(
            "minisbd.models.get_model_file") as download:
            with self.assertRaises(RuntimeError):
                require_local_sentence_model(translation)
            download.assert_not_called()


class BotIntegrationTests(unittest.TestCase):
    def test_failed_result_is_skipped_even_if_legacy_mode_lingers(self):
        from bot import TranslatorBot
        bot = SimpleNamespace(settings=SimpleNamespace(translation_failure_mode="original"))
        self.assertIsNone(TranslatorBot._translation_output(bot, TranslationResult("original", False, 1), "en", "es"))
        self.assertEqual(TranslatorBot._translation_output(bot, TranslationResult("hola", True, 1), "en", "es"), "hola")

    def test_disabled_dedicated_channel_is_not_reaction_topic(self):
        from bot import TranslatorBot
        from settings import CHANNELS_BY_LANG
        channel = SimpleNamespace(id=CHANNELS_BY_LANG["ru"].channel_id)
        bot = SimpleNamespace(settings=SimpleNamespace(reaction_channel_ids={channel.id}, reaction_category_ids=set()))
        self.assertFalse(TranslatorBot._is_reaction_channel(bot, channel))
