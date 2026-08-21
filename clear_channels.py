import os
import asyncio
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Channel IDs you want to completely purge
TARGET_CHANNEL_IDS = [
    1536508569338253332,  # EN
    1536508632785748108,  # ES
    # Add any other specific channel IDs here, or leave just the ones you want to wipe
]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}. Starting purge...")
    
    for channel_id in TARGET_CHANNEL_IDS:
        channel = client.get_channel(channel_id)
        if not channel:
            print(f"⚠️ Could not find channel {channel_id}")
            continue

        print(f"Cleaning channel: #{channel.name} ({channel_id})...")
        
        # 1. Bulk delete messages newer than 14 days
        try:
            deleted = await channel.purge(limit=None)
            print(f"  - Bulk deleted {len(deleted)} messages.")
        except Exception as e:
            print(f"  - Bulk delete warning: {e}")

        # 2. Individually delete remaining old messages (older than 14 days)
        old_count = 0
        async for msg in channel.history(limit=None):
            try:
                await msg.delete()
                old_count += 1
                await asyncio.sleep(0.5)  # Rate limit safety delay
            except Exception as e:
                print(f"  - Failed to delete msg {msg.id}: {e}")

        if old_count > 0:
            print(f"  - Manually deleted {old_count} older messages.")

        print(f"✅ Finished purging #{channel.name}")

    print("🎉 All targeted channels have been cleared!")
    await client.close()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN missing in .env")
    else:
        client.run(TOKEN)