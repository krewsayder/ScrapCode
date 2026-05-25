import pytest
from unittest.mock import MagicMock, patch

from bot.models import Cluster, Guild
from bot.permissions import check_tier, check_guild_member

SERVER_ID   = 12345
ADMIN_ROLE  = 111
OFFICER_ROLE = 222
MEMBER_ROLE  = 333
OTHER_ROLE   = 999

GUILD_A = "word-bearers"
GUILD_B = "night-lords"


def make_cluster(role_tiers=None, guilds=None) -> Cluster:
    guild_objs = {
        gid: Guild(id=gid, name=gid, api_key="", role_id=0, member_role_ids=role_ids)
        for gid, role_ids in (guilds or {}).items()
    }
    return Cluster(
        discord_server_id=SERVER_ID,
        guilds=guild_objs,
        role_tiers=role_tiers or {},
    )


def make_interaction(
    is_admin: bool = False,
    role_ids: list = None,
    namespace_guild_id: str = None,
) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.guild_permissions.administrator = is_admin

    roles = []
    for rid in (role_ids or []):
        role = MagicMock()
        role.id = rid
        roles.append(role)
    interaction.user.roles = roles

    ns = MagicMock()
    ns.guild_id = namespace_guild_id
    interaction.namespace = ns

    return interaction


STANDARD_CLUSTER = make_cluster(
    role_tiers={"admin": [ADMIN_ROLE], "officer": [OFFICER_ROLE]},
    guilds={GUILD_A: [MEMBER_ROLE], GUILD_B: [MEMBER_ROLE]},
)


# ==========================================
# check_tier — admin
# ==========================================

@pytest.mark.asyncio
async def test_tier_admin_discord_admin_passes():
    interaction = make_interaction(is_admin=True)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "admin") is True

@pytest.mark.asyncio
async def test_tier_admin_with_admin_role_passes():
    interaction = make_interaction(role_ids=[ADMIN_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "admin") is True

@pytest.mark.asyncio
async def test_tier_admin_with_officer_role_fails():
    interaction = make_interaction(role_ids=[OFFICER_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "admin") is False

@pytest.mark.asyncio
async def test_tier_admin_no_roles_fails():
    interaction = make_interaction(role_ids=[OTHER_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "admin") is False

@pytest.mark.asyncio
async def test_tier_admin_empty_config_fails():
    interaction = make_interaction(role_ids=[ADMIN_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = make_cluster()
        assert await check_tier(interaction, "admin") is False


# ==========================================
# check_tier — officer (admin cascades in)
# ==========================================

@pytest.mark.asyncio
async def test_tier_officer_with_officer_role_passes():
    interaction = make_interaction(role_ids=[OFFICER_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "officer") is True

@pytest.mark.asyncio
async def test_tier_officer_with_admin_role_passes():
    interaction = make_interaction(role_ids=[ADMIN_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "officer") is True

@pytest.mark.asyncio
async def test_tier_officer_with_member_role_fails():
    interaction = make_interaction(role_ids=[MEMBER_ROLE])
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_tier(interaction, "officer") is False


# ==========================================
# check_guild_member — discord admin
# ==========================================

@pytest.mark.asyncio
async def test_guild_member_discord_admin_passes():
    interaction = make_interaction(is_admin=True, namespace_guild_id=GUILD_A)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is True


# ==========================================
# check_guild_member — cluster tier cascade
# ==========================================

@pytest.mark.asyncio
async def test_guild_member_officer_role_passes_without_member_role():
    interaction = make_interaction(role_ids=[OFFICER_ROLE], namespace_guild_id=GUILD_A)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is True

@pytest.mark.asyncio
async def test_guild_member_admin_role_passes_without_member_role():
    interaction = make_interaction(role_ids=[ADMIN_ROLE], namespace_guild_id=GUILD_A)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is True


# ==========================================
# check_guild_member — specific guild_id
# ==========================================

@pytest.mark.asyncio
async def test_guild_member_correct_guild_passes():
    interaction = make_interaction(role_ids=[MEMBER_ROLE], namespace_guild_id=GUILD_A)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is True

@pytest.mark.asyncio
async def test_guild_member_wrong_guild_fails():
    cluster = make_cluster(
        role_tiers={"admin": [ADMIN_ROLE], "officer": [OFFICER_ROLE]},
        guilds={GUILD_A: [MEMBER_ROLE], GUILD_B: [OTHER_ROLE]},
    )
    interaction = make_interaction(role_ids=[MEMBER_ROLE], namespace_guild_id=GUILD_B)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = cluster
        assert await check_guild_member(interaction) is False

@pytest.mark.asyncio
async def test_guild_member_unknown_guild_fails():
    interaction = make_interaction(role_ids=[MEMBER_ROLE], namespace_guild_id="nonexistent")
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is False


# ==========================================
# check_guild_member — no guild_id in namespace (any-guild check)
# ==========================================

@pytest.mark.asyncio
async def test_guild_member_any_guild_with_member_role_passes():
    interaction = make_interaction(role_ids=[MEMBER_ROLE], namespace_guild_id=None)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is True

@pytest.mark.asyncio
async def test_guild_member_any_guild_no_matching_role_fails():
    interaction = make_interaction(role_ids=[OTHER_ROLE], namespace_guild_id=None)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = STANDARD_CLUSTER
        assert await check_guild_member(interaction) is False

@pytest.mark.asyncio
async def test_guild_member_empty_member_role_ids_fails():
    cluster = make_cluster(
        role_tiers={"admin": [ADMIN_ROLE], "officer": [OFFICER_ROLE]},
        guilds={GUILD_A: [], GUILD_B: []},
    )
    interaction = make_interaction(role_ids=[MEMBER_ROLE], namespace_guild_id=GUILD_A)
    with patch("bot.permissions.repo") as mock_repo:
        mock_repo.load.return_value = cluster
        assert await check_guild_member(interaction) is False