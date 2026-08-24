import asyncio
import tempfile
import unittest
from pathlib import Path

from state import MessageState


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def test_replace_get_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = MessageState(Path(tmp) / "map.sqlite3")
            await state.initialize()
            await state.replace_target(100, 200, [301, 302])
            rows = await state.get(100)
            self.assertEqual([r.webhook_message_id for r in rows], [301, 302])
            await state.replace_target(100, 200, [401])
            rows = await state.get(100)
            self.assertEqual([r.webhook_message_id for r in rows], [401])
            await state.delete_source(100)
            self.assertEqual(await state.get(100), [])

    async def test_translation_metrics_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = MessageState(Path(tmp) / "map.sqlite3")
            await state.initialize()
            await state.record_translation_metric(
                source_message_id=100, kind="automatic", source_lang="en", target_lang="de",
                source_chars=25, ok=True, attempts=1, final_error=None,
                rate_limit_count=0, timeout_count=0, duration_ms=850,
            )
            await state.record_translation_metric(
                source_message_id=100, kind="automatic", source_lang="en", target_lang="fr",
                source_chars=25, ok=False, attempts=3, final_error="TooManyRequests",
                rate_limit_count=2, timeout_count=0, duration_ms=62000,
            )
            await state.record_translation_metric(
                source_message_id=200, kind="reaction", source_lang="auto", target_lang="es",
                source_chars=10, ok=True, attempts=2, final_error=None,
                rate_limit_count=0, timeout_count=1, duration_ms=2400,
            )

            summary = await state.translation_metrics_summary(24)
            self.assertEqual(summary.requests, 3)
            self.assertEqual(summary.succeeded, 2)
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.source_messages, 2)
            self.assertEqual(summary.source_chars, 60)
            self.assertEqual(summary.attempts, 6)
            self.assertEqual(summary.rate_limits, 2)
            self.assertEqual(summary.timeouts, 1)
            self.assertEqual(summary.automatic_requests, 2)
            self.assertEqual(summary.reaction_requests, 1)
            self.assertEqual(summary.top_errors, (("TooManyRequests", 1),))

    async def test_reaction_mapping_dedup_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = MessageState(Path(tmp) / "map.sqlite3")
            await state.initialize()
            await state.replace_reaction(100, 555, "en", [701])
            self.assertTrue(await state.has_reaction(100, "en"))
            self.assertFalse(await state.has_reaction(100, "de"))
            self.assertEqual(await state.reaction_languages(100), ["en"])

            await state.replace_reaction(100, 555, "en", [702, 703])
            rows = await state.get_reactions(100)
            self.assertEqual([r.bot_message_id for r in rows], [702, 703])

            await state.replace_reaction(100, 555, "de", [704])
            self.assertEqual(await state.reaction_languages(100), ["de", "en"])

            await state.delete_reaction_target(100, "en")
            self.assertEqual(await state.reaction_languages(100), ["de"])
            await state.delete_reaction_source(100)
            self.assertEqual(await state.get_reactions(100), [])


if __name__ == "__main__":
    unittest.main()
