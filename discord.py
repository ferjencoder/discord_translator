import os
import discord
from discord.ext import commands
from aiohttp import web

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

# Dummy web server to satisfy Render's port scan
async def handle_health_check(request):
    return web.Response(text="Bot is live!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get("/", handle_handle_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

async def main():
    await start_dummy_server()
    await bot.start(os.environ["DISCORD_TOKEN"])

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())