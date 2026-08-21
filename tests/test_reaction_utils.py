import unittest

from reaction_utils import canonical_flag, label_for_language, language_for_emoji


class ReactionUtilsTests(unittest.TestCase):
    def test_alias_flags_dedupe_to_same_language(self):
        self.assertEqual(language_for_emoji("🇬🇧").lang, "en")
        self.assertEqual(language_for_emoji("🇺🇸").lang, "en")
        self.assertEqual(language_for_emoji("🇵🇹").lang, "pt")
        self.assertEqual(language_for_emoji("🇧🇷").lang, "pt")

    def test_unknown_reaction_is_ignored(self):
        self.assertIsNone(language_for_emoji("👍"))

    def test_canonical_labels(self):
        self.assertEqual(canonical_flag("ceb"), "🇵🇭")
        self.assertEqual(label_for_language("ceb"), "Bisaya")
        self.assertEqual(label_for_language("xx"), "XX")


if __name__ == "__main__":
    unittest.main()
