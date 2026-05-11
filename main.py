import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.update_cog       import setup_update
from cogs.view_cog         import setup_view
from cogs.admin_cog        import setup_admin
from cogs.registration_cog import setup_registration
from cogs.tasks_cog        import setup_tasks
from cogs.fun_cog          import setup_fun
from cogs.bomb_cog         import setup_bomb
from cogs.token_cog        import setup_token
from cogs.replay_cog       import setup_replay

# ==========================================
# CONFIGURATION & INITIALIZATION
# ==========================================

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="a")

intents   = discord.Intents.default()
bot       = commands.Bot(command_prefix="!", intents=intents, help_command=None)
file_lock = asyncio.Lock()


# ==========================================
# BOT EVENTS
# ==========================================
GUILD_IDS = [1458181638453203099]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    
    # Prod variant, remove comments before launching
    # Propogating takes a while so we're doing this in dev
    # await bot.tree.sync()
    # print("Slash commands synced.")

    for guild_id in GUILD_IDS:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to guild {guild_id}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    print(f"Command error: {error}")
    try:
        await interaction.response.send_message(f"Error: {error}", ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(f"Error: {error}", ephemeral=True)

# ==========================================
# COG LOADING
# ==========================================

async def load_cogs():
    # UpdateCog needs the shared file_lock to prevent concurrent file writes
    await setup_update(bot, file_lock)
    await setup_view(bot)
    await setup_admin(bot)
    await setup_registration(bot)
    await setup_tasks(bot, file_lock)
    await setup_fun(bot)
    await setup_bomb(bot)
    await setup_token(bot)
    await setup_replay(bot)



# ==========================================
# EXECUTION
# ==========================================

async def main():
    async with bot:
        discord.utils.setup_logging(handler=handler)
        await load_cogs()
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())