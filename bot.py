from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from aiohttp import web

from settings import CHANNELS_BY_ID, ConfigError, RuntimeChannel, Settings, load_settings
from reaction_utils import canonical_flag, label_for_language, language_for_emoji
from state import MessageState
from text_utils import chunk_text, clean_preview
from translator import TranslationResult, TranslationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ozy-translator")


@dataclass(frozen=True)
class MediaBlob:
    filename: str
    data: bytes
    description: str | None = None


@dataclass(frozen=True)
class TranslationEvent:
    kind: str  # create | edit | delete
    source_message_id: int
    source_channel_id: int
    message: discord.Message | None = None


@dataclass(frozen=True)
class ReactionEvent:
    kind: str  # request | refresh | delete
    source_message_id: int
    source_channel_id: int
    target_lang: str | None = None


class TranslatorBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())

        self.settings = settings
        self.channels_by_id = settings.channels_by_id
        self.state = MessageState(settings.state_db)
        self.translator = TranslationService(
            concurrency=settings.translation_concurrency,
            start_interval_seconds=settings.translation_start_interval_seconds,
            retries=settings.translation_retries,
            connect_timeout_seconds=settings.translation_connect_timeout_seconds,
            read_timeout_seconds=settings.translation_read_timeout_seconds,
            task_timeout_seconds=settings.translation_task_timeout_seconds,
            cooldown_429_seconds=settings.translation_429_cooldown_seconds,
        )

        self.http_session: aiohttp.ClientSession | None = None
        self.webhooks: dict[int, discord.Webhook] = {}
        self.event_queue: asyncio.Queue[TranslationEvent] = asyncio.Queue(maxsize=settings.event_queue_size)
        self.reaction_queue: asyncio.Queue[ReactionEvent] = asyncio.Queue(maxsize=settings.reaction_queue_size)
        self.worker_task: asyncio.Task | None = None
        self.reaction_worker_task: asyncio.Task | None = None
        self.self_ping_task: asyncio.Task | None = None
        self.health_runner: web.AppRunner | None = None
        self._validated_guild = False
        self._reaction_pending: set[tuple[int, str]] = set()
        self._reaction_user_windows: dict[int, deque[float]] = defaultdict(deque)
        self.startup_error: BaseException | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        await self.state.initialize()
        removed = await self.state.cleanup(self.settings.state_retention_days)
        if removed:
            log.info("Removed %d expired translation mapping rows", removed)

        await self._start_health_server()
        await self._validate_and_load_webhooks()
        self.worker_task = asyncio.create_task(self._event_worker(), name="translation-event-worker")
        self.reaction_worker_task = asyncio.create_task(self._reaction_worker(), name="reaction-translation-worker")

        if self.settings.self_ping_enabled and self.settings.render_external_url:
            self.self_ping_task = asyncio.create_task(self._self_ping_loop(), name="render-self-ping")

    async def close(self) -> None:
        tasks = (self.worker_task, self.reaction_worker_task, self.self_ping_task)
        for task in tasks:
            if task and not task.done():
                task.cancel()
        if any(tasks):
            await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)

        if self.health_runner:
            await self.health_runner.cleanup()
            self.health_runner = None

        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

        await super().close()

    async def _start_health_server(self) -> None:
        async def health(_: web.Request) -> web.Response:
            payload = {
                "status": "ok",
                "discord_ready": self.is_ready(),
                "queue_depth": self.event_queue.qsize(),
                "reaction_queue_depth": self.reaction_queue.qsize(),
                "reaction_translation_enabled": bool(self.settings.reaction_channel_ids or self.settings.reaction_category_ids),
                "utc": datetime.now(timezone.utc).isoformat(),
            }
            return web.json_response(payload)

        app = web.Application()
        app.router.add_get("/", health)
        app.router.add_get("/healthz", health)
        self.health_runner = web.AppRunner(app, access_log=None)
        await self.health_runner.setup()
        site = web.TCPSite(self.health_runner, "0.0.0.0", self.settings.port)
        await site.start()
        log.info("Health server listening on port %d", self.settings.port)

    async def _self_ping_loop(self) -> None:
        assert self.http_session is not None
        await asyncio.sleep(30)
        base = self.settings.render_external_url.rstrip("/")
        url = f"{base}/healthz"
        while not self.is_closed():
            try:
                async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status >= 400:
                        log.warning("Self-ping returned HTTP %d", response.status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Self-ping failed: %s", exc)
            await asyncio.sleep(self.settings.self_ping_interval_seconds)

    async def _validate_and_load_webhooks(self) -> None:
        assert self.http_session is not None
        seen_webhook_ids: set[int] = set()

        for runtime in self.settings.channels:
            spec = runtime.spec
            webhook = discord.Webhook.from_url(runtime.webhook_url, session=self.http_session)
            try:
                fetched = await webhook.fetch(prefer_auth=False)
            except Exception as exc:
                raise ConfigError(f"{spec.webhook_env} could not be fetched: {exc}") from exc

            if fetched.id in seen_webhook_ids:
                raise ConfigError(f"Webhook ID {fetched.id} is reused by multiple language channels")
            seen_webhook_ids.add(fetched.id)

            if fetched.channel_id != spec.channel_id:
                raise ConfigError(
                    f"{spec.webhook_env} points to channel {fetched.channel_id}, expected {spec.channel_id} ({spec.lang})"
                )
            if fetched.guild_id is not None and fetched.guild_id != self.settings.server_id:
                raise ConfigError(
                    f"{spec.webhook_env} belongs to guild {fetched.guild_id}, expected {self.settings.server_id}"
                )

            self.webhooks[spec.channel_id] = fetched
            log.info("Validated webhook %-3s -> channel %s", spec.lang.upper(), spec.channel_id)

    async def on_ready(self) -> None:
        if not self._validated_guild:
            try:
                await self._validate_guild_channels()
            except Exception as exc:
                self.startup_error = exc
                log.critical("Startup guild/channel validation failed: %s", exc)
                await self.close()
                return
            self._validated_guild = True
        log.info("Translator bot operational as %s (%s)", self.user, self.user.id if self.user else "?")

    async def _validate_guild_channels(self) -> None:
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            raise ConfigError(f"Bot is not connected to expected SERVER_ID {self.settings.server_id}")

        me = guild.me
        if me is None:
            raise ConfigError("Could not resolve the bot member in the configured guild")

        for runtime in self.settings.channels:
            spec = runtime.spec
            channel = guild.get_channel(spec.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ConfigError(f"Configured channel {spec.channel_id} ({spec.lang}) does not exist as a text channel")
            if channel.name != spec.channel_name:
                log.warning(
                    "Channel %s expected name #%s but is currently #%s; ID is valid so translation remains enabled",
                    spec.channel_id,
                    spec.channel_name,
                    channel.name,
                )
            perms = channel.permissions_for(me)
            if not perms.view_channel or not perms.read_message_history:
                raise ConfigError(f"Bot cannot read required source channel #{channel.name} ({spec.channel_id})")

        for category_id in self.settings.reaction_category_ids:
            category = guild.get_channel(category_id)
            if not isinstance(category, discord.CategoryChannel):
                raise ConfigError(f"REACTION_CATEGORY_IDS contains invalid category ID {category_id}")

        for channel_id in self.settings.reaction_channel_ids:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                raise ConfigError(f"REACTION_CHANNEL_IDS contains invalid text/forum channel ID {channel_id}")

        log.info("Validated guild %s and all %d translation channels", guild.name, len(self.settings.channels))
        if self.settings.reaction_channel_ids or self.settings.reaction_category_ids:
            log.info(
                "Reaction translation enabled for %d explicit channels and %d categories",
                len(self.settings.reaction_channel_ids),
                len(self.settings.reaction_category_ids),
            )

    def _is_reaction_channel(self, channel: object) -> bool:
        # Dedicated language channels always use the automatic fan-out mode only.
        channel_id = getattr(channel, "id", None)
        if channel_id in self.channels_by_id:
            return False
        if channel_id in self.settings.reaction_channel_ids:
            return True

        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if parent and parent.id in self.settings.reaction_channel_ids:
                return True
            category_id = getattr(parent, "category_id", None) if parent else None
            return category_id in self.settings.reaction_category_ids

        category_id = getattr(channel, "category_id", None)
        return category_id in self.settings.reaction_category_ids

    def _allow_reaction_user(self, user_id: int) -> bool:
        now = time.monotonic()
        window = self._reaction_user_windows[user_id]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= self.settings.reaction_user_requests_per_minute:
            return False
        window.append(now)
        return True

    def _enqueue_reaction_event(self, event: ReactionEvent) -> bool:
        pending_key: tuple[int, str] | None = None
        if event.kind == "request" and event.target_lang:
            pending_key = (event.source_message_id, event.target_lang)
            if pending_key in self._reaction_pending:
                return False
            self._reaction_pending.add(pending_key)
        try:
            self.reaction_queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            if pending_key:
                self._reaction_pending.discard(pending_key)
            log.error(
                "Reaction translation queue full; dropping kind=%s source_message=%s",
                event.kind,
                event.source_message_id,
            )
            return False

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.guild.id != self.settings.server_id:
            return
        if message.channel.id not in self.channels_by_id:
            return
        if message.author.bot or message.webhook_id:
            return

        await self.event_queue.put(
            TranslationEvent(
                kind="create",
                source_message_id=message.id,
                source_channel_id=message.channel.id,
                message=message,
            )
        )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id != self.settings.server_id:
            return
        if self.user and payload.user_id == self.user.id:
            return
        if payload.member and payload.member.bot:
            return

        language = language_for_emoji(str(payload.emoji))
        if language is None:
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if not self._is_reaction_channel(channel):
            return

        pending_key = (payload.message_id, language.lang)
        if pending_key in self._reaction_pending:
            return
        if not self._allow_reaction_user(payload.user_id):
            log.warning("Reaction translation user rate limit reached user=%s", payload.user_id)
            return

        self._enqueue_reaction_event(
            ReactionEvent(
                kind="request",
                source_message_id=payload.message_id,
                source_channel_id=payload.channel_id,
                target_lang=language.lang,
            )
        )

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id != self.settings.server_id or "content" not in payload.data:
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if payload.channel_id in self.channels_by_id:
            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Could not fetch edited source message %s: %s", payload.message_id, exc)
                return
            if message.author.bot or message.webhook_id:
                return
            await self.event_queue.put(
                TranslationEvent(
                    kind="edit",
                    source_message_id=message.id,
                    source_channel_id=message.channel.id,
                    message=message,
                )
            )
            return

        if self._is_reaction_channel(channel):
            # Refresh only languages that were previously requested; no new translations are created on edit.
            if await self.state.reaction_languages(payload.message_id):
                self._enqueue_reaction_event(
                    ReactionEvent(
                        kind="refresh",
                        source_message_id=payload.message_id,
                        source_channel_id=payload.channel_id,
                    )
                )

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id != self.settings.server_id:
            return
        if payload.channel_id in self.channels_by_id:
            await self.event_queue.put(
                TranslationEvent(
                    kind="delete",
                    source_message_id=payload.message_id,
                    source_channel_id=payload.channel_id,
                    message=None,
                )
            )
            return

        # State is authoritative here. A deleted thread/channel may no longer be cached,
        # but previously generated reaction translations still need cleanup.
        if await self.state.reaction_languages(payload.message_id):
            self._enqueue_reaction_event(
                ReactionEvent(
                    kind="delete",
                    source_message_id=payload.message_id,
                    source_channel_id=payload.channel_id,
                )
            )

    async def _event_worker(self) -> None:
        while True:
            event = await self.event_queue.get()
            try:
                if event.kind == "delete":
                    await self._delete_translations(event.source_message_id)
                elif event.kind == "edit":
                    await self._delete_translations(event.source_message_id)
                    if event.message:
                        await self._translate_and_dispatch(event.message, edited=True)
                elif event.message:
                    await self._translate_and_dispatch(event.message, edited=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "Unhandled event processing error kind=%s source_message=%s",
                    event.kind,
                    event.source_message_id,
                )
            finally:
                self.event_queue.task_done()

    async def _reaction_worker(self) -> None:
        while True:
            event = await self.reaction_queue.get()
            try:
                if event.kind == "delete":
                    await self._delete_reaction_translations(event.source_message_id)
                elif event.kind == "refresh":
                    await self._refresh_reaction_translations(event.source_message_id, event.source_channel_id)
                elif event.kind == "request" and event.target_lang:
                    await self._handle_reaction_request(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "Unhandled reaction processing error kind=%s source_message=%s target=%s",
                    event.kind, event.source_message_id, event.target_lang,
                )
            finally:
                if event.kind == "request" and event.target_lang:
                    self._reaction_pending.discard((event.source_message_id, event.target_lang))
                self.reaction_queue.task_done()

    async def _fetch_message_for_reaction(self, channel_id: int, message_id: int) -> discord.Message | None:
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not self._is_reaction_channel(channel) or not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not fetch reaction source message %s: %s", message_id, exc)
            return None

    async def _handle_reaction_request(self, event: ReactionEvent) -> None:
        assert event.target_lang is not None
        if await self.state.has_reaction(event.source_message_id, event.target_lang):
            return

        existing = await self.state.reaction_languages(event.source_message_id)
        if len(existing) >= self.settings.reaction_max_translations_per_message:
            log.info(
                "Reaction translation cap reached source=%s cap=%s",
                event.source_message_id, self.settings.reaction_max_translations_per_message,
            )
            return

        message = await self._fetch_message_for_reaction(event.source_channel_id, event.source_message_id)
        if message is None or message.author.bot or message.webhook_id:
            return
        if datetime.now(timezone.utc) - message.created_at > timedelta(days=self.settings.reaction_max_age_days):
            log.info("Ignoring old reaction translation request source=%s", message.id)
            return
        if not message.content.strip():
            return
        if len(message.content) > self.settings.reaction_max_source_chars:
            log.warning("Reaction source message %s exceeds configured character limit", message.id)
            return

        await self._create_reaction_translation(message, event.target_lang)

    async def _build_reaction_contents(self, message: discord.Message, target_lang: str) -> list[str]:
        label = label_for_language(target_lang)
        result = await self.translator.translate(message.content, "auto", target_lang)
        flag = canonical_flag(target_lang)
        header = f"{flag} **{label}**"
        chunks = chunk_text(result.text.strip(), max_length=1800) or [result.text.strip()]
        return [
            f"{header if index == 0 else f'{flag} **{label}** *(continued)*'}\n{chunk}".strip()
            for index, chunk in enumerate(chunks)
        ]

    async def _create_reaction_translation(self, message: discord.Message, target_lang: str) -> None:
        contents = await self._build_reaction_contents(message, target_lang)
        sent: list[discord.Message] = []

        try:
            for content in contents:
                sent_message = await message.reply(
                    content,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    silent=self.settings.silent_reaction_translations,
                )
                sent.append(sent_message)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.error("Failed posting reaction translation source=%s target=%s: %s", message.id, target_lang, exc)
            for sent_message in sent:
                try:
                    await sent_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            return

        await self.state.replace_reaction(
            message.id, message.channel.id, target_lang, [sent_message.id for sent_message in sent]
        )
        log.info(
            "Reaction translation created source=%s target=%s requester-count-deduped=true",
            message.id, target_lang.upper(),
        )

    async def _refresh_reaction_translations(self, source_message_id: int, source_channel_id: int) -> None:
        languages = await self.state.reaction_languages(source_message_id)
        if not languages:
            return
        message = await self._fetch_message_for_reaction(source_channel_id, source_message_id)
        if message is None or message.author.bot or message.webhook_id:
            return
        if not message.content.strip():
            await self._delete_reaction_translations(source_message_id)
            return

        for target_lang in languages:
            await self._update_reaction_translation(message, target_lang)

    async def _update_reaction_translation(self, message: discord.Message, target_lang: str) -> None:
        rows = [r for r in await self.state.get_reactions(message.id) if r.target_lang == target_lang]
        rows.sort(key=lambda row: row.chunk_index)
        if not rows:
            await self._create_reaction_translation(message, target_lang)
            return

        contents = await self._build_reaction_contents(message, target_lang)
        kept_ids: list[int] = []
        common = min(len(rows), len(contents))

        # Edit existing bot replies in place so source edits do not move translations to the bottom.
        for index in range(common):
            partial = message.channel.get_partial_message(rows[index].bot_message_id)
            try:
                edited = await partial.edit(
                    content=contents[index],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                kept_ids.append(edited.id)
            except discord.NotFound:
                # If someone manually removed the bot reply, recreate this chunk below.
                recreated = await message.reply(
                    contents[index],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    silent=self.settings.silent_reaction_translations,
                )
                kept_ids.append(recreated.id)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Failed editing reaction translation %s: %s", rows[index].bot_message_id, exc)
                kept_ids.append(rows[index].bot_message_id)

        # Translation became shorter: remove obsolete continuation chunks.
        for row in rows[common:]:
            try:
                await message.channel.get_partial_message(row.bot_message_id).delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Failed deleting obsolete reaction chunk %s: %s", row.bot_message_id, exc)
                kept_ids.append(row.bot_message_id)

        # Translation became longer: append only the new continuation chunks.
        for content in contents[common:]:
            try:
                sent = await message.reply(
                    content,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    silent=self.settings.silent_reaction_translations,
                )
                kept_ids.append(sent.id)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Failed appending reaction translation chunk source=%s: %s", message.id, exc)

        if kept_ids:
            await self.state.replace_reaction(message.id, message.channel.id, target_lang, kept_ids)
        else:
            await self.state.delete_reaction_target(message.id, target_lang)

    async def _delete_reaction_target_messages(self, source_message_id: int, target_lang: str) -> None:
        rows = [r for r in await self.state.get_reactions(source_message_id) if r.target_lang == target_lang]
        for row in rows:
            channel = self.get_channel(row.source_channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(row.source_channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            if not hasattr(channel, "get_partial_message"):
                continue
            try:
                await channel.get_partial_message(row.bot_message_id).delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("Failed deleting reaction translation %s: %s", row.bot_message_id, exc)
        await self.state.delete_reaction_target(source_message_id, target_lang)

    async def _delete_reaction_translations(self, source_message_id: int) -> None:
        for target_lang in await self.state.reaction_languages(source_message_id):
            await self._delete_reaction_target_messages(source_message_id, target_lang)
        await self.state.delete_reaction_source(source_message_id)

    async def _resolve_reply_context(self, message: discord.Message) -> str:
        ref = message.reference
        if not ref or not ref.message_id:
            return ""

        referenced = ref.resolved
        if isinstance(referenced, discord.Message):
            author = referenced.author.display_name
            preview = clean_preview(referenced.content)
            return f"Replying to {author}: {preview}" if preview else f"Replying to {author}"

        try:
            fetched = await message.channel.fetch_message(ref.message_id)
            preview = clean_preview(fetched.content)
            return f"Replying to {fetched.author.display_name}: {preview}" if preview else f"Replying to {fetched.author.display_name}"
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return "Replying to an earlier message"

    async def _collect_media(self, message: discord.Message) -> tuple[list[MediaBlob], list[str]]:
        blobs: list[MediaBlob] = []
        fallback_urls: list[str] = []
        total_bytes = 0

        # Re-upload normal Discord attachments while within configured safety limits.
        for attachment in message.attachments:
            if len(blobs) >= 10:
                fallback_urls.append(attachment.url)
                continue
            if attachment.size > self.settings.max_reupload_bytes:
                fallback_urls.append(attachment.url)
                continue
            if total_bytes + attachment.size > self.settings.max_total_reupload_bytes:
                fallback_urls.append(attachment.url)
                continue
            try:
                data = await attachment.read(use_cached=True)
                if len(data) > self.settings.max_reupload_bytes:
                    fallback_urls.append(attachment.url)
                    continue
                blobs.append(MediaBlob(attachment.filename, data, attachment.description))
                total_bytes += len(data)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
                log.warning("Attachment download failed %s: %s", attachment.id, exc)
                fallback_urls.append(attachment.url)

        # External GIF/image/video embeds are safest as links.
        for embed in message.embeds:
            if embed.url and embed.type in {"gifv", "image", "video"}:
                fallback_urls.append(embed.url)

        # Re-upload raster/animated Discord stickers. Lottie cannot be converted by discord.py.
        assert self.http_session is not None
        for sticker in message.stickers:
            if sticker.format is discord.StickerFormatType.lottie:
                fallback_urls.append(f"[Sticker: {sticker.name}]")
                continue
            if len(blobs) >= 10:
                fallback_urls.append(f"[Sticker: {sticker.name}]")
                continue
            try:
                url = str(sticker.url)
                headers = {"User-Agent": "Mozilla/5.0 OZY-Discord-Translator/2.0"}
                async with self.http_session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    data = await response.read()
                if len(data) > self.settings.max_reupload_bytes or total_bytes + len(data) > self.settings.max_total_reupload_bytes:
                    fallback_urls.append(f"[Sticker: {sticker.name}]")
                    continue
                ext = "gif" if sticker.format is discord.StickerFormatType.gif else "png"
                blobs.append(MediaBlob(f"sticker_{sticker.id}.{ext}", data))
                total_bytes += len(data)
            except Exception as exc:
                log.warning("Sticker fetch failed %s: %s", sticker.id, exc)
                fallback_urls.append(f"[Sticker: {sticker.name}]")

        # Preserve order while removing duplicate media links/labels.
        fallback_urls = list(dict.fromkeys(fallback_urls))
        return blobs, fallback_urls

    async def _translate_and_dispatch(self, message: discord.Message, *, edited: bool) -> None:
        source_runtime = self.channels_by_id[message.channel.id]
        source_lang = source_runtime.spec.lang

        text = message.content or ""
        reply_context = await self._resolve_reply_context(message)
        if reply_context:
            text = f"[{reply_context}]\n{text}".strip()
        if edited:
            text = f"[Edited]\n{text}".strip()

        media_blobs, media_fallbacks = await self._collect_media(message)
        media_fallbacks = [url for url in media_fallbacks if url not in text]

        if not text.strip() and not media_blobs and not media_fallbacks:
            return

        target_runtimes = [c for c in self.settings.channels if c.spec.channel_id != message.channel.id]

        async def translate_target(target: RuntimeChannel) -> tuple[RuntimeChannel, TranslationResult]:
            if text.strip():
                result = await self.translator.translate(text, source_lang, target.spec.lang)
            else:
                result = TranslationResult(text="", ok=True, attempts=0)
            return target, result

        translated = await asyncio.gather(*(translate_target(target) for target in target_runtimes))

        username = f"{message.author.display_name} ({source_lang.upper()})"
        if len(username) > 80:
            username = username[:79].rstrip() + "…"
        avatar_url = str(message.author.display_avatar.url)

        # Send all destination copies concurrently. Each source event waits for these
        # deliveries to finish before the next event, preserving global conversation order.
        await asyncio.gather(
            *(
                self._send_translation(
                    source_message_id=message.id,
                    target=target,
                    translated=result.text,
                    media_blobs=media_blobs,
                    media_fallbacks=media_fallbacks,
                    username=username,
                    avatar_url=avatar_url,
                )
                for target, result in translated
            )
        )

    async def _send_translation(
        self,
        *,
        source_message_id: int,
        target: RuntimeChannel,
        translated: str,
        media_blobs: list[MediaBlob],
        media_fallbacks: list[str],
        username: str,
        avatar_url: str,
    ) -> None:
        body = translated.strip()
        if media_fallbacks:
            media_text = "\n".join(media_fallbacks)
            body = f"{body}\n{media_text}".strip()

        chunks = chunk_text(body, 1900) if body else [""]
        sent_ids: list[int] = []

        for index, chunk in enumerate(chunks):
            files_for_chunk = media_blobs if index == len(chunks) - 1 else []
            message = await self._webhook_send_with_retry(
                target,
                content=chunk or None,
                blobs=files_for_chunk,
                username=username,
                avatar_url=avatar_url,
            )
            if message is None:
                log.error(
                    "Destination delivery failed source=%s target=%s",
                    source_message_id,
                    target.spec.lang.upper(),
                )
                return
            sent_ids.append(message.id)

        await self.state.replace_target(source_message_id, target.spec.channel_id, sent_ids)

    async def _webhook_send_with_retry(
        self,
        target: RuntimeChannel,
        *,
        content: str | None,
        blobs: list[MediaBlob],
        username: str,
        avatar_url: str,
    ) -> discord.WebhookMessage | None:
        webhook = self.webhooks[target.spec.channel_id]

        for attempt in range(1, self.settings.webhook_retries + 1):
            files = [
                discord.File(io.BytesIO(blob.data), filename=blob.filename, description=blob.description)
                for blob in blobs
            ]
            try:
                kwargs = {
                    "content": content,
                    "username": username,
                    "avatar_url": avatar_url,
                    "allowed_mentions": discord.AllowedMentions.none(),
                    "wait": True,
                    "silent": self.settings.silent_translations,
                }
                if files:
                    kwargs["files"] = files
                return await webhook.send(**kwargs)
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error("Permanent webhook failure for %s: %s", target.spec.lang.upper(), exc)
                return None
            except discord.HTTPException as exc:
                # discord.py handles normal rate-limit buckets. Retry transient server/network failures.
                if exc.status < 500 and exc.status != 429:
                    log.error("Non-retryable webhook HTTP %s for %s: %s", exc.status, target.spec.lang.upper(), exc)
                    return None
                if attempt >= self.settings.webhook_retries:
                    log.error("Webhook retries exhausted for %s: %s", target.spec.lang.upper(), exc)
                    return None
                delay = min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                log.warning("Webhook transient error %s for %s; retrying in %.2fs", exc.status, target.spec.lang.upper(), delay)
                await asyncio.sleep(delay)
            except aiohttp.ClientError as exc:
                if attempt >= self.settings.webhook_retries:
                    log.error("Webhook network retries exhausted for %s: %s", target.spec.lang.upper(), exc)
                    return None
                delay = min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                await asyncio.sleep(delay)

        return None

    async def _delete_translations(self, source_message_id: int) -> None:
        rows = await self.state.get(source_message_id)
        if not rows:
            return

        grouped: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            grouped[row.target_channel_id].append(row.webhook_message_id)

        for target_channel_id, message_ids in grouped.items():
            webhook = self.webhooks.get(target_channel_id)
            if webhook is None:
                continue
            for message_id in message_ids:
                try:
                    await webhook.delete_message(message_id)
                except discord.NotFound:
                    pass
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning("Failed deleting translated message %s: %s", message_id, exc)

        await self.state.delete_source(source_message_id)


async def run_bot(settings: Settings) -> None:
    client = TranslatorBot(settings)
    try:
        await client.start(settings.discord_token)
    finally:
        if not client.is_closed():
            await client.close()
    if client.startup_error:
        raise RuntimeError(f"Startup validation failed: {client.startup_error}") from client.startup_error


def main() -> None:
    try:
        settings = load_settings()
        asyncio.run(run_bot(settings))
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
