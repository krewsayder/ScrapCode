import random
import discord
from discord import app_commands
from discord.ext import commands


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


async def setup_fun(bot: commands.Bot):
    await bot.add_cog(FunCog(bot))
