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

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Persistent session pool for webhooks
http_session: aiohttp.ClientSession = None

@client.event
async def on_ready():
    global http_session
    http_session = aiohttp.ClientSession()
    logging.info(f"Translator bot operational as {client.user}")
    client.loop.create_task(keep_alive_ping())

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
            break
        except Exception as e:
            logging.warning(f"Translation error ({target_lang}) [Attempt {attempt}/{max_retries}]: {e}")
            if attempt == max_retries:
                return f"[Translation temporarily unavailable]\n> {text}"
            await asyncio.sleep(delay)
            delay *= 2

    for placeholder, original in placeholder_map.items():
        translated = translated.replace(placeholder, original)

    return translated

async def post_webhook_with_retry(webhook_url, payload, max_retries=5):
    """Posts payload to Discord Webhook with smart rate-limit backing off."""
    for attempt in range(1, max_retries + 1):
        try:
            async with http_session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    return True
                elif resp.status == 429:
                    data = await resp.json()
                    # Respect Discord's exact retry_after duration
                    retry_after = data.get("retry_after", 1.5)
                    logging.warning(f"Webhook rate limited. Respecting Discord cooldown: {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    logging.warning(f"Webhook Error ({resp.status}) [Attempt {attempt}/{max_retries}]")
        except Exception as e:
            logging.warning(f"Webhook POST failed [Attempt {attempt}/{max_retries}]: {e}")

        await asyncio.sleep(1.0)
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

    logging.info(f"📥 Received message in [{source_lang.upper()}] ({message.channel.name}): '{text_to_translate}'")

    for target_id, config in CHANNEL_MAP.items():
        if target_id == source_channel_id:
            continue

        target_lang = config["lang"]
        webhook_url = config["webhook"]

        if not webhook_url:
            logging.warning(f"❌ Cannot send to [{target_lang.upper()}]: WEBHOOK URL IS MISSING OR NULL!")
            continue

        if text_to_translate.strip():
            translated_text = await translate_text_with_retry(text_to_translate, target_lang)
        else:
            translated_text = ""

        if has_attachments:
            attachments_str = "\n".join(attachment_urls)
            translated_text = f"{translated_text}\n{attachments_str}".strip()

        chunks = chunk_text(translated_text)

        for chunk in chunks:
            payload = {
                "content": chunk,
                "username": f"{message.author.display_name} ({source_lang.upper()})",
                "avatar_url": str(message.author.display_avatar.url)
            }
            success = await post_webhook_with_retry(webhook_url, payload)
            if not success:
                logging.error(f"❌ Failed to post webhook message to [{target_lang.upper()}]")
            
            # Brief pause to stagger webhooks and prevent rate-limiting across routes
            await asyncio.sleep(0.15)

if __name__ == "__main__":
    client.run(TOKEN)