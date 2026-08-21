import os
import re
import time
import io
import asyncio
import threading
import logging
import discord
import aiohttp
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from flask import Flask

# Configure logging to keep console output clean
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Load environment variables
load_dotenv()

# -------------------------------------------------------------------
# FLASK DUMMY SERVER & RENDER KEEP-ALIVE
# -------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def health_check():
    return "OZY Translator Bot is live!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Start Flask once in a daemon thread
threading.Thread(target=run_flask, daemon=True).start()

async def keep_alive_ping():
    """Periodically pings the Flask server to prevent instance sleeping on Render."""
    await asyncio.sleep(10)
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await session.get(render_url)
            except Exception as e:
                logging.warning(f"Keep-alive self-ping failed: {e}")
            await asyncio.sleep(600)  # Ping every 10 minutes

# -------------------------------------------------------------------
# CONFIGURATION MAP: Connect Channel ID -> Language Code & Webhook
# -------------------------------------------------------------------
CHANNEL_MAP = {
    1536508569338253332: {"lang": "en", "webhook": os.getenv("WEBHOOK_EN")},
    1536508632785748108: {"lang": "es", "webhook": os.getenv("WEBHOOK_ES")},
    1536525721584017548: {"lang": "fr", "webhook": os.getenv("WEBHOOK_FR")},
    1536510376617967616: {"lang": "pt", "webhook": os.getenv("WEBHOOK_PT")},
    1536510464144441515: {"lang": "sv", "webhook": os.getenv("WEBHOOK_SV")},
    1536508684081827880: {"lang": "de", "webhook": os.getenv("WEBHOOK_DE")},
    1536508734530920570: {"lang": "ceb", "webhook": os.getenv("WEBHOOK_CEB")},
    1538166128017412096: {"lang": "ru", "webhook": os.getenv("WEBHOOK_RU")},
    1538166161873567794: {"lang": "ar", "webhook": os.getenv("WEBHOOK_AR")},
    1538637390149587025: {"lang": "no", "webhook": os.getenv("WEBHOOK_NO")}
}

TOKEN = os.getenv("DISCORD_TOKEN")
PROTECTION_PATTERN = re.compile(r"(<a?:[a-zA-Z0-9_]+:\d+>|<@!?\d+>|<@&\d+>)")

def chunk_text(text, max_length=1900):
    return [text[i:i + max_length] for i in range(0, len(text), max_length)]

async def translate_text_with_retry(text, target_lang, max_retries=3):
    if not text or not text.strip():
        return ""

    tokens = PROTECTION_PATTERN.findall(text)
    placeholder_map = {}
    protected_text = text

    for idx, token in enumerate(tokens):
        placeholder = f"__TOKEN_{idx}__"
        placeholder_map[placeholder] = token
        protected_text = protected_text.replace(token, placeholder, 1)

    def _sync_translate():
        return GoogleTranslator(source='auto', target=target_lang).translate(protected_text)

    delay = 1.0
    translated = None

    for attempt in range(1, max_retries + 1):
        try:
            translated = await asyncio.to_thread(_sync_translate)

            # Reject raw Google HTML server error pages (Error 500/429)
            if translated and ("Error 500" in translated or "<!DOCTYPE html>" in translated or "That’s an error" in translated):
                logging.warning(f"Google Translator returned HTML Error page on attempt {attempt}")
                translated = None
                raise ValueError("Received HTML error from translator endpoint")

            break
        except Exception as e:
            if attempt == max_retries:
                logging.warning(f"Translation failed for [{target_lang}] after max retries: {e}")
                # Fall back directly to original text rather than posting raw HTML errors
                return text
            await asyncio.sleep(delay)
            delay *= 2

    if translated:
        for placeholder, original in placeholder_map.items():
            translated = translated.replace(placeholder, original)

    return translated or text

async def fetch_sticker_as_static_png(session, sticker):
    """Downloads a static PNG representation for standard, APNG, or Lottie stickers."""
    # Use native sticker url if available or build CDN query endpoint
    url = getattr(sticker, 'url', None)
    
    # Override for Lottie vector stickers or missing URLs to fetch static raster preview
    if not url or getattr(sticker, 'format', None) == discord.StickerFormatType.lottie:
        url = f"https://cdn.discordapp.com/stickers/{sticker.id}.png?size=160"

    filename = f"sticker_{sticker.id}.png"

    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return filename, data
            else:
                # Secondary fallback if direct URL failed
                fallback_url = f"https://cdn.discordapp.com/stickers/{sticker.id}.png?size=160"
                if url != fallback_url:
                    async with session.get(fallback_url) as fallback_resp:
                        if fallback_resp.status == 200:
                            data = await fallback_resp.read()
                            return filename, data
                logging.warning(f"Sticker CDN returned status {resp.status} for sticker {sticker.id}")
    except Exception as e:
        logging.warning(f"Failed to fetch static PNG for sticker {sticker.id}: {e}")
    return None, None

async def post_webhook_payload(session, webhook_url, content, username, avatar_url, file_data_list=None, max_retries=5):
    """Posts payload with optional multipart binary file attachments to Discord Webhook."""
    for attempt in range(1, max_retries + 1):
        try:
            if file_data_list:
                form = aiohttp.FormData()
                payload_json = {
                    "content": content,
                    "username": username,
                    "avatar_url": avatar_url
                }
                form.add_field("payload_json", discord.utils._to_json(payload_json))

                for idx, (filename, b_data) in enumerate(file_data_list):
                    form.add_field(
                        f"file{idx}",
                        io.BytesIO(b_data),
                        filename=filename,
                        content_type="image/png"
                    )

                async with session.post(webhook_url, data=form) as resp:
                    if resp.status in (200, 204):
                        return True
                    elif resp.status == 429:
                        data = await resp.json()
                        retry_after = float(data.get("retry_after", 1.5))
                        await asyncio.sleep(retry_after)
                        continue
            else:
                payload = {
                    "content": content,
                    "username": username,
                    "avatar_url": avatar_url
                }
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status in (200, 204):
                        return True
                    elif resp.status == 429:
                        data = await resp.json()
                        retry_after = float(data.get("retry_after", 1.5))
                        await asyncio.sleep(retry_after)
                        continue
        except Exception as e:
            logging.warning(f"Webhook POST failed [Attempt {attempt}/{max_retries}]: {e}")

        await asyncio.sleep(1.0)
    return False

def build_bot_client():
    """Creates a fresh Discord Client instance."""
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    http_session = None

    async def get_http_session():
        nonlocal http_session
        if http_session is None or http_session.closed:
            http_session = aiohttp.ClientSession()
        return http_session

    @client.event
    async def on_ready():
        await get_http_session()
        logging.info(f"Translator bot operational as {client.user}")
        client.loop.create_task(keep_alive_ping())

    @client.event
    async def on_message(message):
        if message.author.bot or message.webhook_id:
            return

        source_channel_id = message.channel.id

        if source_channel_id not in CHANNEL_MAP:
            return

        source_lang = CHANNEL_MAP[source_channel_id]["lang"]
        text_to_translate = message.content

        # Handle Reply Context
        if message.reference and message.reference.cached_message:
            reply_author = message.reference.cached_message.author.display_name
            text_to_translate = f"*(Replying to {reply_author})*\n{text_to_translate}"

        # Standard file attachments and external GIF embeds
        attachment_urls = [att.url for att in message.attachments]
        embed_urls = [e.url for e in message.embeds if e.url and e.type in ('gifv', 'image', 'video')]

        session = await get_http_session()

        # Download stickers as static PNG image bytes
        sticker_files = []
        failed_sticker_names = []
        for sticker in message.stickers:
            filename, data = await fetch_sticker_as_static_png(session, sticker)
            if filename and data:
                sticker_files.append((filename, data))
            else:
                failed_sticker_names.append(f"[{sticker.name}]")

        media_urls = attachment_urls + embed_urls
        has_media = len(media_urls) > 0 or len(sticker_files) > 0 or len(failed_sticker_names) > 0

        if not text_to_translate.strip() and not has_media:
            return

        for target_id, config in CHANNEL_MAP.items():
            if target_id == source_channel_id:
                continue

            target_lang = config["lang"]
            webhook_url = config["webhook"]

            try:
                if not webhook_url:
                    logging.warning(f"⚠️ Skipped [{target_lang.upper()}]: WEBHOOK URL IS MISSING IN ENV VARS!")
                    continue

                # Add 200ms delay between target language requests to prevent Google IP rate limits
                await asyncio.sleep(0.2)

                if text_to_translate.strip():
                    translated_text = await translate_text_with_retry(text_to_translate, target_lang)
                else:
                    translated_text = ""

                # Append text labels if any sticker failed to convert to PNG
                if failed_sticker_names:
                    translated_text = f"{translated_text}\n{' '.join(failed_sticker_names)}".strip()

                if media_urls:
                    media_str = "\n".join(media_urls)
                    translated_text = f"{translated_text}\n{media_str}".strip()

                chunks = chunk_text(translated_text) if translated_text else [""]
                username = f"{message.author.display_name} ({source_lang.upper()})"
                avatar_url = str(message.author.display_avatar.url)

                for idx, chunk in enumerate(chunks):
                    # Attach static sticker image files on the final chunk
                    files_to_send = sticker_files if idx == len(chunks) - 1 else None

                    success = await post_webhook_payload(
                        session=session,
                        webhook_url=webhook_url,
                        content=chunk,
                        username=username,
                        avatar_url=avatar_url,
                        file_data_list=files_to_send
                    )

                    if not success:
                        logging.error(f"❌ Webhook post failed for [{target_lang.upper()}]")
                    
                    await asyncio.sleep(0.15)

            except Exception as e:
                logging.error(f"💥 Exception caught while dispatching to [{target_lang.upper()}]: {e}")

    return client

if __name__ == "__main__":
    while True:
        try:
            client = build_bot_client()
            client.run(TOKEN)
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logging.warning("⚠️ Discord API global IP rate limit hit on startup. Sleeping 30s before retrying...")
                time.sleep(30)
            else:
                raise e
        except Exception as e:
            logging.error(f"Fatal client error: {e}")
            time.sleep(10)