import json
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import TIER_CHOICES
from bot.permissions import require_guild_member

REPLAY_INDEX_FILE = Path("replay_index.json")

FORUM_CHANNELS = {
    "Avatar": 1481592080940925062,
    "Cawl":1481592218891456583,
    "Ghaz":1481592447845929061,
    "Magnus":1481592865800065037,
    "Mortarion":1481592902059819059,
    "Riptide":1481593074659622912,
    "Rogal Dorn":1481593105831690291,
    "Screamer-Killer":1481593216229707926,
    "Szarekh":1481593184248266792,
    "Tervigon":1481593333192069212,
    "Tyrant":1481593363898695680,
}

MAP_THREADS = {
    "Tervigon":{
        "GB_01":1481676928090898475,
        "GB_02":1481676970612883527,
        "GB_03":1481677016473534464,
        "GB_04":1481677055337693365,
        "GB_05":1481677104038023258,
        "GB_06":1481677151622135921,
        "GB_support_01":1481677960657375353,
        "GB_support_02":1481677995541397715,
        "GB_support_03":1481678034489839626,
        "GB_support_04":1481678181223366797,
        "GB_support_05":1481678254871023800,
        "GB_support_06":1481678289730011146,




    },
    "Tyrant": {
        "GB_01": 1481677221348507772,
        "GB_02": 1481677275597639790,
        "GB_03": 1481677478711005296,
        "GB_04": 1481677514018656457,
        "GB_05": 1481677621606613083,
        "GB_06": 1481677662199222344,
        "GB_support_01": 1481678326757331027,
        "GB_support_02": 1481678362274693252,
        "GB_support_03": 1481678396483436554,
        "GB_support_04": 1481678430368956496,
        "GB_support_05": 1481678466716799183,
        "GB_support_06": 1481678504574844979,

    },
    "Avatar": {
        "GB_Khaine_01": 1481592319894618304,
        "GB_Khaine_02": 1481592399720611840,
        "GB_Khaine_03": 1481595045185589320,
        "GB_Khaine_04": 1481595099925188660,
        "Aethana GB_Khaine_support_01":1481595238060392639,
        "Eldryon GB_Khaine_support_02":1481595289839075349,
        "Aethana GB_Khaine_support_03":1481595332054749256,
        "Eldryon GB_Khaine_support_04":1481595381124173864,
        "Aethana GB_Khaine_support_05":1481595429111201795,
        "Eldryon GB_Khaine_support_06":1481595469665669182,

    },
    "Cawl": {
        "GB_Belisarius_01": 1481596799201317006,
        "GB_Belisarius_02": 1481596839865094226,
        "GB_Belisarius_03": 1481596895716708404,
        "GB_Belisarius_04": 1481596928599916606,
        "Tan Gida GB_Belisarius_support_01":1481596967225262173,
        "Actus GB_Belisarius_support_02":1481597012750241882,
        "Tan Gida GB_Belisarius_support_03":1481597055670550538,
        "Actus GB_Belisarius_support_04":1481597096782991484,
        "Tan Gida GB_Belisarius_support_05":1481597132329979915,
        "Actus GB_Belisarius_support_06":1481597165045289081,



    },
    "Ghaz": {
        "GB_Dakka_01": 1481597329130786899,
        "GB_Dakka_02": 1481597359443021885,
        "GB_Dakka_03": 1481597414111711365,
        "GB_Dakka_03_1":1481597446063919136,
        "GB_Dakka_04": 1481597494038233128,
        "GB_Dakka_05":1481597528939167844,

        "Gibba GB_Dakka_support_01":1481597584459038730,
        "Tanksmasha GB_Dakka_support_02":1481597621134164001,
        "Gibba GB_Dakka_support_03":1481597653023195206,
        "Tanksmasha GB_Dakka_support_04":1481597684987990056,
        "Tanksmasha GB_Dakka_support_04_1":1481597735416102972,
        "Gibba GB_Dakka_support_05":1481597769310277733,
        "Gibba GB_Dakka_support_05_1":1481597810892738631,
        "Tanksmasha GB_Dakka_support_06":1481597843784339597,



    },
"Mortarion": {
        "GB_Mortarion_01": 1481631207966900226,
        "GB_Mortarion_02": 1481631262266490900,
        "GB_Mortarion_03": 1481631296093683832,
        "GB_Mortarion_04": 1481631337302458461,
        "Rotbone GB_Mortarion_support_01":1481631415203401880,
        "Corrodius GB_Mortarion_support_02":1481631454890037330,
        "Rotbone GB_Mortarion_support_03":1481631494953898075,
        "Corrodius GB_Mortarion_support_04":1481631543825793144,
        "Rotbone GB_Mortarion_support_05":1481631587618525308,
        "Corrodius GB_Mortarion_support_06":1481631616928452628,

    },
"Riptide": {
        "GB_Riptide_01": 1481633511898222726,
        "GB_Riptide_02": 1481633548854362245,
        "GB_Riptide_03": 1481633589455224842,
        "Sho GB_Riptide_support_01":1481633675014570066,
        "Sho GB_Riptide_support_02":1481633721613291530,
        "Sho GB_Riptide_support_03":1481633751652896829,
        "Sho GB_Riptide_support_04":1481633778156961882,
        "Revas GB_Riptide_support_01":1481633844594741268,
        "Revas GB_Riptide_support_02":1481633874470637640,
        "Revas GB_Riptide_support_03":1481633904916955226,
        "Revas GB_Riptide_support_04":1481633937045590178,

    },
"Magnus": {
        "GB_Magnus_01": 1481628315767803934,
        "GB_Magnus_02": 1481628397636681738,
        "GB_Magnus_03": 1481628448140034080,
        "GB_Magnus_04": 1481628501575467078,
        "Abraxas GB_Magnus_support_01":1481628609838841948,
        "Thaumachus GB_Magnus_support_02":1481628689253793802,
        "Abraxas GB_Magnus_support_03":1481628746606968925,
        "Thaumachus GB_Magnus_support_04":1481628804236705975,
        "Abraxas GB_Magnus_support_05":1481628867943989332,
        "Thaumachus GB_Magnus_support_06":1481628946289397760,

    },
"Rogal Dorn": {
        "GB_RogalDorn_01": 1481631818703699989,
        "GB_RogalDorn_02": 1481631865642287166,
        "GB_RogalDorn_03": 1481631917261717515,
        "GB_RogalDorn_04": 1481631944188887223,
        "GB_RogalDorn_05": 1481631968776028261,
        "GB_RogalDorn_06": 1486177123147452518,
        "Sibyll GB_RogalDorn_support_01":1481632100045033663,
        "Thad GB_RogalDorn_support_02":1481632131234005093,
        "Sibyll GB_RogalDorn_support_03":1481632160908709989,
        "Thad GB_RogalDorn_support_04":1481632195088093184,
        "Sibyll GB_RogalDorn_support_05":1481632224037048382,
        "Thad GB_RogalDorn_support_06":1481632252772483163,

    },
"Screamer-Killer": {
        "GB_Screamer_01": 1481640960050860135,
        "GB_Screamer_02": 1481640998449844244,
        "GB_Screamer_03": 1481641030129422497,
        "GB_Screamer_04": 1481641065189474504,
        "Neuro GB_Screamer_support_01":1481641151386882272,
        "Neuro GB_Screamer_support_02":1481641275966099476,
        "Neuro GB_Screamer_support_03":1481641362968285376,
        "Neuro GB_Screamer_support_04":1481641448188284939,
        "Neuro GB_Screamer_support_05":1481641558326513686,
        "Neuro GB_Screamer_support_06":1481641695941496915,
        "Neuro GB_Screamer_support_07":1481641806373589103,
        "Neuro GB_Screamer_support_08":1481641912652795936,
        "Winged Prime GB_Screamer_support_01":1481641206718009435,
        "Winged Prime GB_Screamer_support_02":1481641316206247976,
        "Winged Prime GB_Screamer_support_03":1481641409361481880,
        "Winged Prime GB_Screamer_support_04":1481641496506536138,
        "Winged Prime GB_Screamer_support_05":1481641626945196063,
        "Winged Prime GB_Screamer_support_06":1481641756507377674,
        "Winged Prime GB_Screamer_support_07":1481641863059476480,
        "Winged Prime GB_Screamer_support_08":1481641951349440563,

    },
"Szarekh": {
        "GB_SK_01": 1481671657293877288,
        "GB_SK_02": 1481671700021117061,
        "GB_SK_03": 1481671744350584873,
        "GB_SK_04": 1481671790072823880,
        "Left GB_SK_support_01":1481671844137271349,
        "Left GB_SK_support_02":1481671881802121342,
        "Left GB_SK_support_03":1481671948248547328,
        "Left GB_SK_support_04":1481672061364732227,
        "Left GB_SK_support_05":1481672094717579455,
        "Left GB_SK_support_06":1481672129056608470,
        "Left GB_SK_support_07":1481672167459524830,
        "Left GB_SK_support_08":1481672208395931648,
        "Right GB_SK_support_01":1481672258224259143,
        "Right GB_SK_support_02":1481672302813909254,
        "Right GB_SK_support_03":1481672336901148815,
        "Right GB_SK_support_04":1481672385756401815,
        "Right GB_SK_support_05":1481672424960299029,
        "Right GB_SK_support_06":1481672461647876258,
        "Right GB_SK_support_07":1481672531067801600,
        "Right GB_SK_support_08":1481672568674058271,

    },

}

POSITION_CHOICES = [
    app_commands.Choice(name="LHS",     value="LHS"),
    app_commands.Choice(name="RHS",     value="RHS"),
    app_commands.Choice(name="Default", value="Default"),
]

TEAM_CHOICES = [
    app_commands.Choice(name="Neuro",       value="Neuro"),
    app_commands.Choice(name="Laviscus",    value="Laviscus"),
    app_commands.Choice(name="Mech",        value="Mech"),
    app_commands.Choice(name="Battlesuit",  value="Battlesuit"),
    app_commands.Choice(name="RA",          value="RA"),
    app_commands.Choice(name="MH",          value="MH"),
    app_commands.Choice(name="Other",       value="Other"),
]


def load_replay_index() -> dict:
    if not REPLAY_INDEX_FILE.exists():
        return {}
    try:
        return json.loads(REPLAY_INDEX_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_replay_index(data: dict):
    REPLAY_INDEX_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


def build_index_message(entries: list) -> str:
    if not entries:
        return "*No replays submitted yet.*"

    by_team = {}
    for entry in entries:
        by_team.setdefault(entry["team"], {}).setdefault(entry["tier"], []).append(entry)

    lines = []
    team_order = [t.value for t in TEAM_CHOICES]
    tier_order = [t.name for t in TIER_CHOICES]

    for team_name in team_order:
        if team_name not in by_team:
            continue
        lines.append(f"**{team_name}**")
        for tier_name in tier_order:
            if tier_name not in by_team[team_name]:
                continue
            lines.append(f"*{tier_name}*")
            for e in by_team[team_name][tier_name]:
                pos     = f" • {e['position']}" if e.get("position") else ""
                dmg     = f" • {e['damage']}" if e.get("damage") else ""
                comment = f" — {e['comment']}" if e.get("comment") else ""
                lines.append(f"[replay]({e['url']}){pos}{dmg}{comment}")
        lines.append("")

    return "\n".join(lines).strip()


async def boss_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=boss, value=boss)
        for boss in MAP_THREADS
        if current.lower() in boss.lower()
    ][:25]


async def map_autocomplete(interaction: discord.Interaction, current: str):
    boss = interaction.namespace.boss
    maps = MAP_THREADS.get(boss, {})
    return [
        app_commands.Choice(name=m, value=m)
        for m in maps
        if current.lower() in m.lower()
    ][:25]


class ReplayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_thread(self, boss: str, thread_id: int) -> Optional[discord.Thread]:
        forum_channel_id = FORUM_CHANNELS.get(boss)
        if not forum_channel_id:
            print(f"[replay] No forum channel ID for boss {boss}")
            return None

        print(f"[replay] Fetching forum channel {forum_channel_id}...")
        forum = self.bot.get_channel(forum_channel_id)
        if forum is None:
            try:
                forum = await self.bot.fetch_channel(forum_channel_id)
            except Exception as e:
                print(f"[replay] Failed to fetch forum channel: {e}")
                return None

        print(f"[replay] Forum: {forum}, looking for thread {thread_id}...")

        thread = forum.get_thread(thread_id)
        if thread:
            print(f"[replay] Found active thread: {thread}")
            return thread

        try:
            async for t in forum.archived_threads():
                if t.id == thread_id:
                    print(f"[replay] Found archived thread: {t}")
                    return t
        except Exception as e:
            print(f"[replay] Failed to check archived threads: {e}")

        print(f"[replay] Thread {thread_id} not found")
        return None

    async def _edit_index_message(self, boss: str, map_name: str, entries: list):
        thread_id = MAP_THREADS.get(boss, {}).get(map_name)
        if not thread_id:
            return

        data   = load_replay_index()
        msg_id = data.get(boss, {}).get(map_name, {}).get("index_message_id")
        if not msg_id:
            return

        thread = await self._get_thread(boss, thread_id)
        if thread is None:
            return

        try:
            msg     = await thread.fetch_message(msg_id)
            content = build_index_message(entries)
            await msg.edit(content=content)
        except Exception as e:
            print(f"[replay] Failed to edit index message in {boss}/{map_name}: {e}")

    # ==========================================
    # SLASH COMMAND: UPLOAD_REPLAY
    # ==========================================

    @app_commands.command(name="upload_replay", description="Submit a raid replay to the index.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss this replay is for",
        map_name="The map this replay was played on",
        team="Team type used",
        tier="Boss tier",
        damage="Damage dealt (e.g. 1.33M)",
        url="Link to the replay",
        position="Starting position (optional)",
        comment="Optional notes about the run",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    @app_commands.choices(team=TEAM_CHOICES, tier=TIER_CHOICES, position=POSITION_CHOICES)
    async def upload_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        team: app_commands.Choice[str],
        tier: app_commands.Choice[str],
        damage: str,
        url: str,
        position: Optional[app_commands.Choice[str]] = None,
        comment: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        print(f"[replay] upload_replay called — boss={boss} map={map_name}")

        if boss not in MAP_THREADS or map_name not in MAP_THREADS[boss]:
            await interaction.followup.send(f"❌ **{boss} / {map_name}** not found in the index.", ephemeral=True)
            return

        print(f"[replay] Validation passed, loading index...")
        data = load_replay_index()

        for b, maps in data.items():
            for m, mdata in maps.items():
                for entry in mdata.get("entries", []):
                    if entry["url"] == url:
                        await interaction.followup.send(
                            f"❌ This replay URL has already been submitted under **{b} / {m}**.", ephemeral=True)
                        return

        thread_id = MAP_THREADS[boss][map_name]
        print(f"[replay] Getting thread {thread_id}...")
        thread = await self._get_thread(boss, thread_id)
        if thread is None:
            await interaction.followup.send(
                f"❌ Could not find thread for **{boss} / {map_name}**. Check thread ID.", ephemeral=True)
            return

        print(f"[replay] Thread found: {thread}, proceeding...")
        boss_data = data.setdefault(boss, {})
        map_data  = boss_data.setdefault(map_name, {"index_message_id": None, "entries": []})

        if not map_data["index_message_id"]:
            msg = await thread.send("*No replays submitted yet.*")
            map_data["index_message_id"] = msg.id

        map_data["entries"].append({
            "team":         team.value,
            "tier":         tier.name,
            "position":     position.value if position else "",
            "damage":       damage,
            "url":          url,
            "comment":      comment or "",
            "submitted_by": str(interaction.user.id),
        })
        save_replay_index(data)

        await self._edit_index_message(boss, map_name, map_data["entries"])
        await interaction.followup.send(f"✅ Replay submitted for **{boss} / {map_name}**!", ephemeral=True)
        print(f"[replay] Upload complete for {boss}/{map_name}")

    # ==========================================
    # SLASH COMMAND: GET_REPLAY
    # ==========================================

    @app_commands.command(name="get_replay", description="View replays for a map, optionally filtered by team.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss to look up",
        map_name="The map to look up",
        team="Filter by team (optional)",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    @app_commands.choices(team=TEAM_CHOICES)
    async def get_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        team: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer()

        data     = load_replay_index()
        map_data = data.get(boss, {}).get(map_name)

        if not map_data or not map_data.get("entries"):
            await interaction.followup.send(f"No replays found for **{boss} / {map_name}**.")
            return

        entries = map_data["entries"]
        if team:
            entries = [e for e in entries if e["team"] == team.value]
            if not entries:
                await interaction.followup.send(
                    f"No **{team.value}** replays found for **{boss} / {map_name}**.")
                return

        title   = f"**{boss} — {map_name}**"
        if team:
            title += f" • {team.value}"
        content = title + "\n" + build_index_message(entries)

        await interaction.followup.send(content)

    # ==========================================
    # SLASH COMMAND: DELETE_REPLAY
    # ==========================================

    @app_commands.command(name="delete_replay", description="Remove a replay from the index by its URL.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss the replay belongs to",
        map_name="The map the replay belongs to",
        url="The replay URL to delete",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    async def delete_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        url: str,
    ):
        await interaction.response.defer(ephemeral=True)

        data     = load_replay_index()
        map_data = data.get(boss, {}).get(map_name)

        if not map_data:
            await interaction.followup.send(f"❌ No entries found for **{boss} / {map_name}**.", ephemeral=True)
            return

        match = next((e for e in map_data["entries"] if e["url"] == url), None)
        if not match:
            await interaction.followup.send("❌ No replay with that URL found.", ephemeral=True)
            return

        map_data["entries"].remove(match)
        save_replay_index(data)

        await self._edit_index_message(boss, map_name, map_data["entries"])
        await interaction.followup.send(f"✅ Replay removed from **{boss} / {map_name}**.", ephemeral=True)


async def setup_replay(bot: commands.Bot):
    await bot.add_cog(ReplayCog(bot))