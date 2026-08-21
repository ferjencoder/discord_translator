import unittest

from text_utils import chunk_text, protect_text, restore_text


class TextUtilsTests(unittest.TestCase):
    def test_protect_restore_discord_and_tb_tokens(self):
        original = (
            "Go <@123456789012345678> to K:1030 X:512 Y:418, see <#987654321098765432> "
            "at <t:1770000000:R> https://example.com/a?b=1 and use `!stack 10`."
        )
        protected = protect_text(original)
        self.assertNotIn("https://example.com", protected.text)
        self.assertNotIn("K:1030 X:512 Y:418", protected.text)
        restored = restore_text(protected.text, protected.replacements)
        self.assertEqual(restored, original)

    def test_chunk_length(self):
        text = ("word " * 1500).strip()
        chunks = chunk_text(text, 1900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 1900 for chunk in chunks))

    def test_does_not_split_normal_url(self):
        prefix = "hello " * 300
        url = "https://example.com/" + "a" * 250
        suffix = " world" * 300
        chunks = chunk_text(prefix + url + suffix, 1900)
        self.assertTrue(any(url in chunk for chunk in chunks))

    def test_code_block_round_trip(self):
        original = "Translate this\n```python\nprint('do not translate')\n```\nThanks"
        protected = protect_text(original)
        self.assertNotIn("print('do not translate')", protected.text)
        self.assertEqual(restore_text(protected.text, protected.replacements), original)


if __name__ == "__main__":
    unittest.main()
