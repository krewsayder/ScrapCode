import asyncio
import logging
import os
import subprocess
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot import VERSION


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"

from bot.cogs.update_cog       import setup_update
from bot.cogs.view_cog         import setup_view
from bot.cogs.admin_cog        import setup_admin
from bot.cogs.registration_cog import setup_registration
from bot.cogs.tasks_cog        import setup_tasks
from bot.cogs.fun_cog          import setup_fun
from bot.cogs.bomb_cog         import setup_bomb
from bot.cogs.token_cog        import setup_token
from bot.cogs.replay_cog       import setup_replay
from bot.services.chronicl3r.client         import chronicl3rClient
from bot.services.chronicl3r.player_service import PlayerService

# ==========================================
# CONFIGURATION & INITIALIZATION
# ==========================================

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

# ADR-006 D6: `file_lock` (the process-wide asyncio.Lock guarding only
# process_api_response) is RETIRED — SQLite WAL transactions are the
# atomicity boundary (one transaction per guild). DEVOPS NIT-4 fold-in:
# `FileHandler` is replaced by `RotatingFileHandler` (10 MB x 5 files) so
# `discord.log` no longer grows unbounded (disk-full risk on the single VM).
handler = RotatingFileHandler(
    filename="discord.log", encoding="utf-8", mode="a",
    maxBytes=10 * 1024 * 1024, backupCount=5,
)

intents = discord.Intents.default()
bot     = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ==========================================
# BOT EVENTS
# ==========================================
DEV_GUILD_IDS = [1458181638453203099]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} — v{VERSION} ({_git_hash()})")

    # Dev: sync instantly to known guilds. In prod, swap for: await bot.tree.sync()
    for guild_id in DEV_GUILD_IDS:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to guild {guild_id}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[on_guild_join] Joined {guild.name} (id={guild.id})")


@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[on_guild_remove] Removed from {guild.name} (id={guild.id})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.CheckFailure):
        msg = "❌ You don't have permission to use this command."
    else:
        print(f"Command error: {error}")
        msg = f"❌ An error occurred: {error}"
    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(msg, ephemeral=True)

# ==========================================
# COG LOADING
# ==========================================

async def load_cogs():
    chronicl3r_client = chronicl3rClient()
    chronicl3r_client.authenticate()
    player_service = PlayerService(chronicl3r_client)

    await setup_update(bot, player_service)
    await setup_view(bot)
    await setup_admin(bot, player_service)
    await setup_registration(bot)
    await setup_tasks(bot, player_service)
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