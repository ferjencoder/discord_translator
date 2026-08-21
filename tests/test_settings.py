import os
import unittest
from unittest.mock import patch

from settings import CHANNEL_SPECS, ConfigError, load_settings, validate_webhook_url


class SettingsTests(unittest.TestCase):
    def _env(self):
        env = {
            "DISCORD_TOKEN": "test-token",
            "SERVER_ID": "123456789012345678",
            "SELF_PING_ENABLED": "false",
        }
        for i, spec in enumerate(CHANNEL_SPECS, start=1):
            env[spec.webhook_env] = (
                f"https://discord.com/api/webhooks/{100000000000000000 + i}/"
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            )
        return env

    def test_full_config_loads(self):
        with patch.dict(os.environ, self._env(), clear=True):
            settings = load_settings()
        self.assertEqual(settings.server_id, 123456789012345678)
        self.assertEqual(len(settings.channels), 10)
        self.assertFalse(settings.self_ping_enabled)
        self.assertEqual(settings.reaction_channel_ids, frozenset())
        self.assertEqual(settings.reaction_category_ids, frozenset())

    def test_non_discord_webhook_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate_webhook_url("https://example.com/api/webhooks/123/abcdefghijklmnopqrstuvwxyz", "WEBHOOK_X")

    def test_reaction_ids_load_and_dedupe(self):
        env = self._env()
        env["REACTION_CHANNEL_IDS"] = "111, 222,111"
        env["REACTION_CATEGORY_IDS"] = "333,444"
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.reaction_channel_ids, frozenset({111, 222}))
        self.assertEqual(settings.reaction_category_ids, frozenset({333, 444}))

    def test_invalid_reaction_id_is_rejected(self):
        env = self._env()
        env["REACTION_CHANNEL_IDS"] = "111,nope"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
