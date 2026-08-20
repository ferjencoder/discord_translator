import os
import csv
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERVER_ID = os.getenv("SERVER_ID")

if not TOKEN or not SERVER_ID:
    print("❌ Error: DISCORD_TOKEN or SERVER_ID is missing from your .env file.")
    exit(1)

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}. Fetching channel permissions...")
    guild = client.get_guild(int(SERVER_ID))

    if not guild:
        print("❌ Server not found! Double check your SERVER_ID in .env.")
        await client.close()
        return

    with open("channel_permissions_export.csv", mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Category", "Channel Name", "Target (Role/Member)", "Type", "Allowed Permissions", "Denied Permissions"])

        for channel in guild.channels:
            category_name = channel.category.name if channel.category else "No Category"
            
            for target, overwrite in channel.overwrites.items():
                target_name = target.name if hasattr(target, 'name') else str(target)
                target_type = "Role" if isinstance(target, discord.Role) else "Member"

                allowed = [perm for perm, val in overwrite if val is True]
                denied = [perm for perm, val in overwrite if val is False]

                writer.writerow([
                    category_name,
                    channel.name,
                    target_name,
                    target_type,
                    ", ".join(allowed) if allowed else "None",
                    ", ".join(denied) if denied else "None"
                ])

    print("✅ Export complete! Saved to 'channel_permissions_export.csv'")
    await client.close()

if __name__ == "__main__":
    client.run(TOKEN)