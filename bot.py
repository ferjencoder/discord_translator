import os
import asyncio
import threading
import discord
import aiohttp
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
from flask import Flask

# Load environment variables from .env if running locally
load_dotenv()

# -------------------------------------------------------------------
# FLASK DUMMY SERVER FOR RENDER PORT SCAN
# -------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def health_check():
    return "OZY Translator Bot is live!", 200

def run_flask():
    # Render assigns the PORT dynamically via environment variables
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Run Flask in a background thread so it doesn't block Discord
threading.Thread(target=run_flask, daemon=True).start()

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
    }
}

TOKEN = os.getenv("DISCORD_TOKEN")

# Initialize Discord Client with Message Content Intent
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Translator bot operational as {client.user}")

# Helper function to run blocking GoogleTranslator in a separate thread
def translate_text(text, target_lang):
    return GoogleTranslator(source='auto', target=target_lang).translate(text)

@client.event
async def on_message(message):
    # Ignore messages sent by bots or webhooks to avoid infinite loops
    if message.author.bot or message.webhook_id:
        return

    source_channel_id = message.channel.id

    if source_channel_id not in CHANNEL_MAP:
        return

    source_lang = CHANNEL_MAP[source_channel_id]["lang"]
    text_to_translate = message.content

    # Ignore empty messages (e.g. attachments only)
    if not text_to_translate.strip():
        return

    print(f"DEBUG: Received '{text_to_translate}' from [{source_lang}]")

    # Translate and broadcast to all other channels
    async with aiohttp.ClientSession() as session:
        for target_id, config in CHANNEL_MAP.items():
            if target_id == source_channel_id:
                continue

            target_lang = config["lang"]
            webhook_url = config["webhook"]

            if not webhook_url:
                print(f"WARNING: Webhook URL missing for language [{target_lang}]")
                continue

            try:
                # Run the blocking translation call asynchronously
                translated_text = await asyncio.to_thread(translate_text, text_to_translate, target_lang)

                payload = {
                    "content": translated_text,
                    "username": f"{message.author.display_name} ({source_lang.upper()})",
                    "avatar_url": str(message.author.display_avatar.url)
                }

                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        print(f"Webhook Error ({target_lang}): Status {resp.status}")

            except Exception as e:
                print(f"Translation Error ({target_lang}): {e}")

# ALWAYS keep client.run(TOKEN) at the VERY BOTTOM of your script
if __name__ == "__main__":
    client.run(TOKEN)