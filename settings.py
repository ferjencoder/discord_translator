from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChannelSpec:
    lang: str
    label: str
    channel_id: int
    channel_name: str
    role_name: str
    webhook_env: str


CHANNEL_SPECS: tuple[ChannelSpec, ...] = (
    ChannelSpec("en", "English",    1536508569338253332, "english",    "EN", "WEBHOOK_EN"),
    ChannelSpec("es", "Spanish",    1536508632785748108, "español",    "ES", "WEBHOOK_ES"),
    ChannelSpec("fr", "French",     1536525721584017548, "français",   "FR", "WEBHOOK_FR"),
    ChannelSpec("pt", "Portuguese", 1536510376617967616, "português",  "PT", "WEBHOOK_PT"),
    ChannelSpec("sv", "Swedish",    1536510464144441515, "svenska",    "SE", "WEBHOOK_SV"),
    ChannelSpec("de", "German",     1536508684081827880, "deutsch",    "DE", "WEBHOOK_DE"),
    ChannelSpec("ceb", "Bisaya",    1536508734530920570, "bisaya",     "PH", "WEBHOOK_CEB"),
    ChannelSpec("ru", "Russian",    1538166128017412096, "русский",    "RU", "WEBHOOK_RU"),
    ChannelSpec("ar", "Arabic",     1538166161873567794, "العربية",    "AR", "WEBHOOK_AR"),
    ChannelSpec("no", "Norwegian",  1538637390149587025, "norsk",      "NO", "WEBHOOK_NO"),
)

CHANNELS_BY_ID = {spec.channel_id: spec for spec in CHANNEL_SPECS}
CHANNELS_BY_LANG = {spec.lang: spec for spec in CHANNEL_SPECS}
CATEGORY_NAME = "💬 OZY Chats"
TRANSLATOR_ROLE_NAME = "OZY Translator"

_ALLOWED_WEBHOOK_HOSTS = {"discord.com", "canary.discord.com", "ptb.discord.com"}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true/false, got: {raw!r}")


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _env_id_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return frozenset()
    values: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ConfigError(f"{name} must contain comma-separated numeric Discord IDs") from exc
        if value <= 0:
            raise ConfigError(f"{name} IDs must be positive")
        values.add(value)
    return frozenset(values)


def validate_webhook_url(value: str, env_name: str) -> str:
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigError(f"{env_name} is not a valid URL") from exc

    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_WEBHOOK_HOSTS:
        raise ConfigError(f"{env_name} must be an https://discord.com webhook URL")

    parts = [p for p in parsed.path.split("/") if p]
    try:
        webhook_idx = parts.index("webhooks")
        webhook_id = parts[webhook_idx + 1]
        webhook_token = parts[webhook_idx + 2]
    except (ValueError, IndexError) as exc:
        raise ConfigError(f"{env_name} does not look like a Discord webhook URL") from exc

    if not webhook_id.isdigit() or len(webhook_token) < 20:
        raise ConfigError(f"{env_name} does not contain a valid webhook ID/token")

    return value


@dataclass(frozen=True)
class RuntimeChannel:
    spec: ChannelSpec
    webhook_url: str


@dataclass(frozen=True)
class Settings:
    discord_token: str
    server_id: int
    channels: tuple[RuntimeChannel, ...]
    port: int
    render_external_url: str | None
    self_ping_enabled: bool
    self_ping_interval_seconds: int
    event_queue_size: int
    translation_concurrency: int
    translation_start_interval_seconds: float
    translation_retries: int
    translation_connect_timeout_seconds: float
    translation_read_timeout_seconds: float
    translation_task_timeout_seconds: float
    translation_429_cooldown_seconds: float
    webhook_retries: int
    max_reupload_bytes: int
    max_total_reupload_bytes: int
    silent_translations: bool
    reaction_channel_ids: frozenset[int]
    reaction_category_ids: frozenset[int]
    reaction_queue_size: int
    reaction_max_age_days: int
    reaction_max_translations_per_message: int
    reaction_max_source_chars: int
    reaction_user_requests_per_minute: int
    silent_reaction_translations: bool
    state_db: Path
    state_retention_days: int

    @property
    def channels_by_id(self) -> dict[int, RuntimeChannel]:
        return {c.spec.channel_id: c for c in self.channels}


def load_identity() -> tuple[str, int]:
    token = _required("DISCORD_TOKEN")
    try:
        server_id = int(_required("SERVER_ID"))
    except ValueError as exc:
        raise ConfigError("SERVER_ID must be a numeric Discord guild ID") from exc
    return token, server_id


def load_settings() -> Settings:
    token, server_id = load_identity()

    channels: list[RuntimeChannel] = []
    for spec in CHANNEL_SPECS:
        url = validate_webhook_url(_required(spec.webhook_env), spec.webhook_env)
        channels.append(RuntimeChannel(spec=spec, webhook_url=url))

    state_db = Path(os.getenv("STATE_DB", "data/message_map.sqlite3")).expanduser()

    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip() or None
    self_ping_default = render_url is not None

    return Settings(
        discord_token=token,
        server_id=server_id,
        channels=tuple(channels),
        port=_env_int("PORT", 10000, 1),
        render_external_url=render_url,
        self_ping_enabled=_env_bool("SELF_PING_ENABLED", self_ping_default),
        self_ping_interval_seconds=_env_int("SELF_PING_INTERVAL_SECONDS", 600, 60),
        event_queue_size=_env_int("EVENT_QUEUE_SIZE", 500, 10),
        translation_concurrency=_env_int("TRANSLATION_CONCURRENCY", 3, 1),
        translation_start_interval_seconds=_env_float("TRANSLATION_START_INTERVAL_SECONDS", 0.20, 0.0),
        translation_retries=_env_int("TRANSLATION_RETRIES", 3, 1),
        translation_connect_timeout_seconds=_env_float("TRANSLATION_CONNECT_TIMEOUT_SECONDS", 5.0, 0.1),
        translation_read_timeout_seconds=_env_float("TRANSLATION_READ_TIMEOUT_SECONDS", 15.0, 0.1),
        translation_task_timeout_seconds=_env_float("TRANSLATION_TASK_TIMEOUT_SECONDS", 25.0, 1.0),
        translation_429_cooldown_seconds=_env_float("TRANSLATION_429_COOLDOWN_SECONDS", 20.0, 1.0),
        webhook_retries=_env_int("WEBHOOK_RETRIES", 3, 1),
        max_reupload_bytes=_env_int("MAX_REUPLOAD_BYTES", 8 * 1024 * 1024, 0),
        max_total_reupload_bytes=_env_int("MAX_TOTAL_REUPLOAD_BYTES", 20 * 1024 * 1024, 0),
        silent_translations=_env_bool("SILENT_TRANSLATIONS", False),
        reaction_channel_ids=_env_id_set("REACTION_CHANNEL_IDS"),
        reaction_category_ids=_env_id_set("REACTION_CATEGORY_IDS"),
        reaction_queue_size=_env_int("REACTION_QUEUE_SIZE", 100, 10),
        reaction_max_age_days=_env_int("REACTION_MAX_AGE_DAYS", 7, 1),
        reaction_max_translations_per_message=_env_int("REACTION_MAX_TRANSLATIONS_PER_MESSAGE", 5, 1),
        reaction_max_source_chars=_env_int("REACTION_MAX_SOURCE_CHARS", 4000, 100),
        reaction_user_requests_per_minute=_env_int("REACTION_USER_REQUESTS_PER_MINUTE", 15, 1),
        silent_reaction_translations=_env_bool("SILENT_REACTION_TRANSLATIONS", True),
        state_db=state_db,
        state_retention_days=_env_int("STATE_RETENTION_DAYS", 30, 1),
    )
