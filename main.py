import discord
from discord.ext import commands
import logging
import asyncio
import os
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('discord')

# Load Environment Variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.members = True
        super().__init__(command_prefix='!', intents=intents)

    async def setup_hook(self):
        # Load Cogs with error handling
        cogs = ['cogs.music', 'cogs.team']
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ Cog loaded: {cog}")
            except Exception as e:
                logger.error(f"❌ Failed to load cog {cog}: {e}")

        # Sync Commands
        logger.info("Syncing commands...")
        await self.tree.sync()
        logger.info("Commands synced.")

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')

async def main():
    bot = MyBot()
    if not TOKEN:
        logger.error("No token found. Please check your .env file.")
        return
    
    async with bot:
        await bot.start(TOKEN)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

