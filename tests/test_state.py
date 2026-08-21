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
