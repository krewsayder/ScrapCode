"""/scrapcode_help must keep pace with /registration.

The help text is a hand-written table in `fun_cog.HELP_DATA`, so nothing ties
it to the commands it describes. These tests fail when a /registration
subcommand is added without a help line, or when acting on another player
is not listed where the officers who may do it will look.
"""
import os

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

from bot.cogs.fun_cog import HELP_DATA
from bot.cogs.registration_cog import RegistrationCog


def _help_names(tier):
    return [name for name, _ in HELP_DATA[tier]["commands"]]


def _registration_subcommands():
    for command in RegistrationCog.__cog_app_commands__:
        if command.name == "registration":
            return {child.name for child in command.commands}
    raise AssertionError("no /registration group on RegistrationCog")


def test_every_registration_subcommand_appears_in_help():
    documented = " ".join(name for tier in HELP_DATA for name in _help_names(tier))
    missing = {sub for sub in _registration_subcommands()
               if f"/registration {sub}" not in documented}
    assert not missing, f"/scrapcode_help does not mention: {sorted(missing)}"


def test_officer_help_lists_acting_on_another_player():
    officer = _help_names("officer")
    assert any(n.startswith("/registration register") and "target_user" in n for n in officer)
    assert any(n.startswith("/registration unregister") and "user_id" in n for n in officer)


def test_every_help_page_fits_one_discord_embed():
    # Discord: at most 25 fields per embed, 256 chars per field name, 1024 per value.
    for tier, page in HELP_DATA.items():
        assert len(page["commands"]) <= 25, tier
        for name, description in page["commands"]:
            assert len(name) <= 256 and len(description) <= 1024, name
