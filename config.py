from discord import app_commands

# ==========================================
# EMBED LIMITS
# ==========================================

FIELD_LIMIT = 1024
MAX_FIELDS  = 20

# ==========================================
# ENCOUNTER LABELS
# ==========================================

LABELS = {"0": "Main", "1": "Left", "2": "Right"}

# ==========================================
# TIER CHOICES (used in slash command options)
# ==========================================

TIER_CHOICES = [
    app_commands.Choice(name="Legendary 1", value="Legendary_0"),
    app_commands.Choice(name="Legendary 2", value="Legendary_1"),
    app_commands.Choice(name="Legendary 3", value="Legendary_2"),
    app_commands.Choice(name="Legendary 4", value="Legendary_3"),
    app_commands.Choice(name="Legendary 5", value="Legendary_4"),
    app_commands.Choice(name="Mythic 1",    value="Mythic"),
    app_commands.Choice(name="Mythic 2",    value="Mythic_1"),
]

# ==========================================
# REQUIRED ROLES (for restricted commands)
# ==========================================

REQUIRED_ROLES = ("Dark Tech", "Tech-Priest", "Captain","Guild Leader")

# ==========================================
# CAP DETECT CHANNEL
# ==========================================

# The Discord channel ID where cap_detect will post token cap pings.

CAP_CHANNEL_ID = REDACTED

# ==========================================
# AUTO UPDATE CHANNEL
# ==========================================

UPDATE_CHANNEL_ID = REDACTED

# ==========================================
# REPLAY INDEX CHANNEL
# ==========================================

REPLAY_INDEX_CHANNEL_ID = REDACTED