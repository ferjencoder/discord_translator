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
        self.assertEqual(len(settings.channels), 8)
        self.assertFalse(settings.self_ping_enabled)
        self.assertEqual(settings.reaction_channel_ids, frozenset())
        self.assertEqual(settings.reaction_category_ids, frozenset())
        self.assertEqual(settings.discord_startup_429_initial_backoff_seconds, 300.0)
        self.assertEqual(settings.discord_startup_429_max_backoff_seconds, 3600.0)
        self.assertEqual(settings.translation_concurrency, 1)
        self.assertEqual(settings.translation_start_interval_seconds, 0.05)
        self.assertEqual(settings.translation_retries, 1)
        self.assertEqual(settings.translation_failure_mode, "skip")
        self.assertEqual(settings.translation_metrics_retention_days, 90)
        self.assertEqual(settings.translator_status_role_names, frozenset({"Leader", "Superior", "Superiors"}))
        self.assertEqual(settings.webhook_retries, 2)
        self.assertEqual(settings.webhook_start_interval_seconds, 0.50)
        self.assertEqual(settings.webhook_429_cooldown_seconds, 10.0)
        self.assertEqual(settings.webhook_max_retry_after_seconds, 30.0)
        self.assertEqual(settings.webhook_quarantine_seconds, 300.0)
        self.assertEqual(settings.webhook_send_timeout_seconds, 15.0)
        self.assertEqual(settings.webhook_delivery_timeout_seconds, 25.0)

    def test_non_discord_webhook_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate_webhook_url("https://example.com/api/webhooks/123/abcdefghijklmnopqrstuvwxyz", "WEBHOOK_X")

    def test_inactive_webhooks_are_not_required(self):
        env = self._env()
        for code in ("SV", "RU"):
            del env["WEBHOOK_" + code]
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual({c.spec.lang for c in settings.channels}, {"en", "es", "ar", "de", "fr", "no", "pt", "it"})

    def test_subset_requires_only_its_webhooks(self):
        env = self._env()
        env["ACTIVE_TRANSLATION_LANGS"] = "en,es"
        for key in list(env):
            if key.startswith("WEBHOOK_") and key not in {"WEBHOOK_EN", "WEBHOOK_ES"}:
                del env[key]
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(len(load_settings().channels), 2)

    def test_unsupported_language_and_unsafe_settings_rejected(self):
        for key, value in (("ACTIVE_TRANSLATION_LANGS", "en,ru"),
                           ("ACTIVE_TRANSLATION_LANGS", "es"),
                           ("TRANSLATION_CONCURRENCY", "2"),
                           ("TRANSLATION_FAILURE_MODE", "original")):
            env = self._env()
            env[key] = value
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ConfigError):
                    load_settings()

    def test_reaction_ids_load_and_dedupe(self):
        env = self._env()
        env["REACTION_CHANNEL_IDS"] = "111, 222,111"
        env["REACTION_CATEGORY_IDS"] = "333,444"
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.reaction_channel_ids, frozenset({111, 222}))
        self.assertEqual(settings.reaction_category_ids, frozenset({333, 444}))


    def test_translation_failure_mode_validation(self):
        env = self._env()
        env["TRANSLATION_FAILURE_MODE"] = "skip"
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual(settings.translation_failure_mode, "skip")

        env["TRANSLATION_FAILURE_MODE"] = "nope"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_invalid_reaction_id_is_rejected(self):
        env = self._env()
        env["REACTION_CHANNEL_IDS"] = "111,nope"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
