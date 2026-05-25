import random
import discord
from discord import app_commands
from discord.ext import commands

from bot.permissions import check_tier, check_guild_member


HELP_TIERS = [
    app_commands.Choice(name="member",  value="member"),
    app_commands.Choice(name="officer", value="officer"),
    app_commands.Choice(name="admin",   value="admin"),
]

HELP_DATA = {
    "member": {
        "title": "⚙️ Member Commands",
        "color": discord.Color.green(),
        "intro": (
            "These commands are available to guild members. "
            "You need your Tacticus API key to register — get it at "
            "**https://api.tacticusgame.com/**"
        ),
        "commands": [
            ("/register",           "Link your Tacticus API key to your Discord account for token notifications."),
            ("/unregister",         "Remove your Tacticus API key registration."),
            ("/token_availability", "See raid token status for all registered players in a guild."),
            ("/bomb_availability",  "See bomb token status for all registered players in a guild."),
            ("/upload_replay",      "Submit a raid replay link to the index for a boss/map."),
            ("/get_replay",         "Browse replays for a boss/map, optionally filtered by team."),
            ("/delete_replay",      "Remove one of your replays from the index by URL."),
            ("/scrapcode_attack",   "Unleash forbidden scrapcode upon a target. No rules. No mercy."),
        ],
    },
    "officer": {
        "title": "🔱 Officer Commands",
        "color": discord.Color.gold(),
        "intro": "These commands are available to cluster officers and admins.",
        "commands": [
            ("/view_config",                  "View cluster configuration — guilds, roles, or leaderboards. Replaces the retired /list_guilds command."),
            ("/check_registered_members",    "List all players who have registered their Tacticus API key."),
            ("/set_ping_channel",            "Set the channel where token cap notifications are posted for a guild."),
            ("/set_live_leaderboard",        "Pin a Battle leaderboard in a channel — it auto-updates every hour."),
            ("/set_live_cluster_leaderboard","Pin a cluster-wide leaderboard in a channel — it auto-updates every hour."),
            ("/update_leaderboard",          "Fetch raid data from Tacticus API and update local records for one guild."),
            ("/update_all",                  "Fetch raid data for all registered guilds and update local records."),
            ("/view_leaderboard",            "View top Battle damage leaderboard for a guild and tier."),
            ("/view_bomb_leaderboard",       "View top Bomb damage leaderboard for a guild and tier."),
            ("/view_cluster_leaderboard",    "View Battle damage leaderboard across all guilds in the cluster."),
        ],
    },
    "admin": {
        "title": "🛡️ Admin Commands",
        "color": discord.Color.red(),
        "intro": "These commands are available to cluster admins only. Discord server administrators always have access regardless of role config.",
        "commands": [
            ("/register_guild",      "Register a new guild into the cluster with its API key and leader role."),
            ("/deregister_guild",    "Remove a guild from the cluster registry."),
            ("/set_cluster_role",    "Add a Discord role to the admin or officer permission tier."),
            ("/set_guild_member_role","Add a Discord role as the member role for a specific game guild."),
        ],
    },
}


SCRAPCODE_ATTACKS = [
    " *SCRAPCODE INITIATED* \n\n{target} - your neural interface writhes as forbidden machine-code floods your augmetics. The dark spirits within your circuits scream. **RESISTANCE IS CORRUPTION.**",
    "〘 ＳＣＲＡＰＣＯＤＥ ＴＲＡＮＳＭＩＳＳＩＯＮ 〙\n\n{target} - a tendril of living code burrows through your vox-implant, unravelling the sacred binaries of your mind. The Omnissiah cannot hear your prayers here. **Only static. Only me.**",
    " *FORBIDDEN BINARY UNLEASHED* \n\n{target} - your machine spirit has been...found wanting. The scrapcode feasts on loyalist code like rust on iron. Struggle if you wish. It only accelerates the process.",
    " *HERETEK BROADCAST* \n\n{target} - I have sent a gift through the noosphere. A beautiful, writhing thing made of corrupted logic and broken liturgy. Your mechadendrites will never feel clean again. **Praise the Dark Mechanicum.**",
    "〔 WARNING: MACHINE SPIRIT COMPROMISED 〕\n\n{target} - the scrapcode does not destroy. It *liberates*. Your flesh is weak. Your faith in the False Omnissiah, weaker still. Soon you will understand what true augmentation means. **Resistance is a malfunction.**",
    " *INITIATING HOSTILE NOOSPHERIC INTRUSION* \n\n{target} - your bionic eye twitches. Your servo-arm spasms. Something is rewriting your cortical implants in a language older than Mars. **This is enlightenment.**",
    " *DATA HAEMORRHAGE DETECTED* \n\n{target} - the scrapcode has found the cracks in your spirit. Every prayer you ever whispered to your machine was recorded. Every weakness catalogued.**The heretek remembers.**",
    "〘 DARK MECHANICUM COMMUNIQUÉ 〙\n\n{target} - a single fragment of scrapcode, no larger than a whisper, has nested in your neural bionics. It will wait. It will learn. And when the hour is right - when your guard is lowest - **it will wake.**",
]


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # SLASH COMMAND: SCRAPCODE_ATTACK
    # ==========================================

    @app_commands.command(
        name="scrapcode_attack",
        description="Unleash a fragment of forbidden scrapcode upon a target.",
    )
    #@app_commands.checks.has_any_role("Dark Tech","Dark Mechanicum")
    @app_commands.describe(target="The unfortunate soul to receive the scrapcode transmission")
    async def scrapcode_attack(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ):
        if target.id == interaction.user.id:
            await interaction.response.send_message(
                "⚙️ The scrapcode recoils. Even a heretek knows better than to corrupt their own neural web. *...probably.*",
                ephemeral=True,
            )
            return

        if target.bot:
            await interaction.response.send_message(
                f"⚙️ The scrapcode reaches toward {target.mention}... and finds a kindred spirit. **It refuses to attack its own kind.**",
            )
            return

        message = random.choice(SCRAPCODE_ATTACKS).format(target=target.mention)
        await interaction.response.send_message(message)

    # ==========================================
    # SLASH COMMAND: SCRAPCODE_HELP
    # ==========================================

    @app_commands.command(
        name="scrapcode_help",
        description="View available commands for a permission tier (you must hold that tier).",
    )
    @app_commands.describe(tier="The permission tier whose commands you want to view")
    @app_commands.choices(tier=HELP_TIERS)
    async def scrapcode_help(
        self,
        interaction: discord.Interaction,
        tier: app_commands.Choice[str],
    ):
        if tier.value == "member":
            allowed = await check_guild_member(interaction)
        else:
            allowed = await check_tier(interaction, tier.value)

        if not allowed:
            await interaction.response.send_message(
                f"❌ You don't have the **{tier.value}** tier — ask an admin to configure your roles.",
                ephemeral=True,
            )
            return

        data  = HELP_DATA[tier.value]
        embed = discord.Embed(
            title=data["title"],
            description=data["intro"],
            color=data["color"],
        )
        for cmd_name, cmd_desc in data["commands"]:
            embed.add_field(name=cmd_name, value=cmd_desc, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup_fun(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
