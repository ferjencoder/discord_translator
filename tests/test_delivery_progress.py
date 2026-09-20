import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from bot import TranslatorBot
from translator import TranslationResult


class DeliveryProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_destination_posts_before_slow_translation_finishes(self):
        source = SimpleNamespace(spec=SimpleNamespace(channel_id=1, lang="en"))
        fast = SimpleNamespace(spec=SimpleNamespace(channel_id=2, lang="es"))
        slow = SimpleNamespace(spec=SimpleNamespace(channel_id=3, lang="it"))
        release = asyncio.Event()
        delivered = asyncio.Event()
        async def translate(**kwargs):
            if kwargs["target_lang"] == "it":
                await release.wait()
            return TranslationResult("translated", True, 1)
        async def send(**kwargs):
            if kwargs["target"].spec.lang == "es":
                delivered.set()
        bot = SimpleNamespace(
            channels_by_id={1: source},
            settings=SimpleNamespace(channels=(source, fast, slow), webhook_delivery_timeout_seconds=2),
            _webhook_quarantine_until={},
            _resolve_reply_context=AsyncMock(return_value=None),
            _collect_media=AsyncMock(return_value=([], [])),
            _translate_tracked=AsyncMock(side_effect=translate),
            _send_translation=AsyncMock(side_effect=send),
            _translation_output=lambda result, source, target: result.text if result.ok else None)
        message = SimpleNamespace(id=1, channel=SimpleNamespace(id=1), content="hello",
                                  author=SimpleNamespace(display_name="Tester",
                                                         display_avatar=SimpleNamespace(url="avatar")))
        task = asyncio.create_task(TranslatorBot._translate_and_dispatch(bot, message, edited=False))
        try:
            await asyncio.wait_for(delivered.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual(bot._send_translation.await_count, 1)
        finally:
            release.set()
            await task
        self.assertEqual(bot._send_translation.await_count, 2)
