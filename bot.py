import discord
import aiohttp
from deep_translator import GoogleTranslator

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

TOKEN = DISCORD_TOKEN

# Initialize Discord Client with Message Content Intent
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Translator bot operational as {client.user}")

@client.event
async def on_message(message):
    # Ignore messages sent by bots or webhooks to avoid infinite loops
    if message.author.bot or message.webhook_id:
        return

    source_channel_id = message.channel.id
    print(f"DEBUG: Message received in channel ID: {source_channel_id}")

    if source_channel_id not in CHANNEL_MAP:
        print(f"DEBUG: Channel ID {source_channel_id} is NOT in CHANNEL_MAP.")
        return

    source_lang = CHANNEL_MAP[source_channel_id]["lang"]
    text_to_translate = message.content
    print(f"DEBUG: Received '{text_to_translate}' from source language [{source_lang}]")

    # Ignore empty messages (e.g. attachments only)
    if not text_to_translate.strip():
        return

    # Translate and broadcast to all other channels
    async with aiohttp.ClientSession() as session:
        for target_id, config in CHANNEL_MAP.items():
            if target_id == source_channel_id:
                continue

            target_lang = config["lang"]
            webhook_url = config["webhook"]

            try:
                translated_text = GoogleTranslator(
                    source='auto', 
                    target=target_lang
                ).translate(text_to_translate)

                payload = {
                    "content": translated_text,
                    "username": f"{message.author.display_name} ({source_lang.upper()})",
                    "avatar_url": str(message.author.display_avatar.url)
                }

                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status not in (200, 204):
                        print(f"Webhook Error ({target_lang}): Status {resp.status}")
                    else:
                        print(f"DEBUG: Successfully sent translation to [{target_lang}]")

            except Exception as e:
                print(f"Translation Error ({target_lang}): {e}")

# ALWAYS keep client.run(TOKEN) at the VERY BOTTOM of your script
client.run(TOKEN)