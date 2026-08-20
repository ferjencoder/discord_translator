import os
import re
import asyncio
import threading
import logging
import discord
import aiohttp
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from flask import Flask

# Configure logging
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

# Run Flask in a background thread
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
                async with session.get(render_url) as resp:
                    logging.info(f"Keep-alive self-ping status: {resp.status}")
            except Exception as e:
                logging.warning(f"Keep-alive self-ping failed: {e}")
            await asyncio.sleep(600)  # Ping every 10 minutes

# -------------------------------------------------------------------
# CONFIGURATION MAP: Connect Channel ID -> Language Code & Webhook
# -------------------------------------------------------------------
CHANNEL_MAP = {
    1536508569338253332: {
        "lang": "en",
        "webhook": os.getenv("WEBHOOK_EN")
    },
    1536508632785748108: {
        "lang": "es",
        "webhook": os.getenv("WEBHOOK_ES")
    },
    1536525721584017548: {
        "lang": "fr",
        "webhook": os.getenv("WEBHOOK_FR")
    },
    1536510376617967616: {
        "lang": "pt",
        "webhook": os.getenv("WEBHOOK_PT")
    },
    1536510464144441515: {
        "lang": "sv",
        "webhook": os.getenv("WEBHOOK_SV")
    },
    1536508684081827880: {
        "lang": "de",
        "webhook": os.getenv("WEBHOOK_DE")
    },
    1536508734530920570: {
        "lang": "ceb",  # Bisaya / Cebuano
        "webhook": os.getenv("WEBHOOK_CEB")
    },
    1538166128017412096: {
        "lang": "ru",
        "webhook": os.getenv("WEBHOOK_RU")
    },
    1538166161873567794: {
        "lang": "ar",
        "webhook": os.getenv("WEBHOOK_AR")
    },
    1538637390149587025: {
        "lang": "no",
        "webhook": os.getenv("WEBHOOK_NO")
    }
}

TOKEN = os.getenv("DISCORD_TOKEN")

# Regex to capture Discord custom emojis, user mentions, and role mentions
PROTECTION_PATTERN = re.compile(r"(<a?:[a-zA-Z0-9_]+:\d+>|<@!?\d+>|<@&\d+>)")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    logging.info(f"Translator bot operational as {client.user}")
    client.loop.create_task(keep_alive_ping())

def chunk_text(text, max_length=1900):
    """Splits text into chunks under Discord's 2000 character limit."""
    return [text[i:i + max_length] for i in range(0, len(text), max_length)]

async def translate_text_with_retry(text, target_lang, max_retries=3):
    if not text or not text.strip():
        return ""

    # Mask custom emojis and mentions before sending to Google Translate
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
            break
        except Exception as e:
            logging.warning(f"Translation error ({target_lang}) [Attempt {attempt}/{max_retries}]: {e}")
            if attempt == max_retries:
                return f"[Translation temporarily unavailable]\n> {text}"
            await asyncio.sleep(delay)
            delay *= 2

    # Restore original emojis and mentions back into translated output
    for placeholder, original in placeholder_map.items():
        translated = translated.replace(placeholder, original)

    return translated

async def post_webhook_with_retry(session, webhook_url, payload, max_retries=3):
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    return True
                elif resp.status == 429:
                    retry_after = (await resp.json()).get("retry_after", delay)
                    logging.warning(f"Webhook rate limited. Waiting {retry_after}s...")
                    await asyncio.sleep(retry_after)
                else:
                    logging.warning(f"Webhook Error ({resp.status}) [Attempt {attempt}/{max_retries}]")
        except Exception as e:
            logging.warning(f"Webhook POST failed [Attempt {attempt}/{max_retries}]: {e}")

        if attempt < max_retries:
            await asyncio.sleep(delay)
            delay *= 2
    return False

@client.event
async def on_message(message):
    if message.author.bot or message.webhook_id:
        return

    source_channel_id = message.channel.id

    if source_channel_id not in CHANNEL_MAP:
        return

    source_lang = CHANNEL_MAP[source_channel_id]["lang"]
    text_to_translate = message.content

    attachment_urls = [att.url for att in message.attachments]
    has_attachments = len(attachment_urls) > 0

    if not text_to_translate.strip() and not has_attachments:
        return

    logging.info(f"Received message from [{source_lang.upper()}]: '{text_to_translate}'")

    async with aiohttp.ClientSession() as session:
        for target_id, config in CHANNEL_MAP.items():
            if target_id == source_channel_id:
                continue

            target_lang = config["lang"]
            webhook_url = config["webhook"]

            if not webhook_url:
                logging.warning(f"Webhook URL missing for language [{target_lang.upper()}]")
                continue

            # 1. Translate content
            if text_to_translate.strip():
                translated_text = await translate_text_with_retry(text_to_translate, target_lang)
            else:
                translated_text = ""

            # 2. Append attachment URLs
            if has_attachments:
                attachments_str = "\n".join(attachment_urls)
                translated_text = f"{translated_text}\n{attachments_str}".strip()

            # 3. Chunk text if over Discord limit
            chunks = chunk_text(translated_text)

            # 4. Dispatch payloads
            for chunk in chunks:
                payload = {
                    "content": chunk,
                    "username": f"{message.author.display_name} ({source_lang.upper()})",
                    "avatar_url": str(message.author.display_avatar.url)
                }
                await post_webhook_with_retry(session, webhook_url, payload)

if __name__ == "__main__":
    client.run(TOKEN)