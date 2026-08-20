import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_ID = os.getenv("SERVER_ID")

CATEGORY_NAME = "💬 OZY Chats"

if not TOKEN or not SERVER_ID:
    print("❌ Error: DISCORD_TOKEN or SERVER_ID is missing from your .env file.")
    exit(1)

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}. Searching for category '{CATEGORY_NAME}'...")
    guild = client.get_guild(int(SERVER_ID))

    if not guild:
        print("❌ Server not found! Double check your SERVER_ID in .env.")
        await client.close()
        return

    # Find the target category
    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if not category:
        print(f"❌ Category '{CATEGORY_NAME}' not found in the server.")
        await client.close()
        return

    print(f"Found category '{category.name}'. Updating channel overwrites...\n")

    # Define standard messaging permissions for regular language roles & bot
    permissions_to_enable = {
        "read_messages": True,
        "send_messages": True,
        "read_message_history": True,
        "embed_links": True,
        "attach_files": True,
        "add_reactions": True,
        "use_external_emojis": True,
        "send_messages_in_threads": True,
    }

    for channel in category.text_channels:
        print(f"⚙️ Updating #{channel.name}...")

        # 1. Block @everyone from reading/writing in language channels by default
        # (This ensures users only see channels matching their assigned role)
        everyone_role = guild.default_role
        await channel.set_permissions(
            everyone_role,
            read_messages=False,
            send_messages=False,
            reason="Bulk permission update script"
        )

        # 2. Update all existing role/member overwrites in this channel
        for target, overwrite in channel.overwrites.items():
            if target == everyone_role:
                continue

            # Apply the permissions dictionary
            overwrite.update(**permissions_to_enable)
            await channel.set_permissions(
                target,
                overwrite=overwrite,
                reason="Bulk permission update script for translation bot"
            )
            target_name = getattr(target, "name", str(target))
            print(f"   └─ Granted full messaging permissions to: {target_name}")

    print("\n✅ All channel permissions in '💬 OZY Chats' have been updated successfully!")
    await client.close()

if __name__ == "__main__":
    client.run(TOKEN)